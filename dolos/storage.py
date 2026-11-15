"""Private document storage; local files for development, R2 across deployments."""
import hashlib
import os
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
CONTENT_TYPES = {'.csv': 'text/csv', '.json': 'application/json', '.pdf': 'application/pdf'}


class StorageError(RuntimeError):
    pass


class UploadNotReady(StorageError):
    pass


def hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_file(path, sha256, size=None):
    actual = path.stat().st_size
    if not 0 < actual <= MAX_FILE_BYTES or (size is not None and actual != size):
        raise ValueError('Uploaded file size does not match the declared size or exceeds 25 MiB.')
    if hash_file(path) != sha256:
        raise ValueError('Uploaded file SHA-256 does not match the declared checksum.')


class LocalStorage:
    backend = 'local'
    bucket = None

    def __init__(self, root):
        self.directory = Path(root) / 'uploads'
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, path, sha256, suffix):
        key = sha256 + suffix
        target = self.directory / key
        # Atomic publication, also when scratch storage is on a different filesystem.
        with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as output:
            temporary = Path(output.name)
            try:
                with Path(path).open('rb') as source:
                    shutil.copyfileobj(source, output)
                output.close()
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return {'backend': self.backend, 'bucket': None, 'object_key': key,
                'byte_size': Path(path).stat().st_size}

    @contextmanager
    def materialize(self, location, sha256, suffix):
        key = location['object_key'] if location else sha256 + suffix
        if Path(key).name != key:
            raise StorageError('Invalid local document key.')
        path = self.directory / key
        validate_file(path, sha256, location['byte_size'] if location else None)
        yield path


class R2Storage:
    backend = 'r2'

    def __init__(self):
        account = os.environ.get('R2_ACCOUNT_ID')
        self.bucket = os.environ.get('R2_BUCKET_NAME')
        access = os.environ.get('R2_ACCESS_KEY_ID')
        secret = os.environ.get('R2_SECRET_ACCESS_KEY')
        if not all((account, self.bucket, access, secret)):
            raise StorageError('Set R2_ACCOUNT_ID, R2_BUCKET_NAME, R2_ACCESS_KEY_ID, and R2_SECRET_ACCESS_KEY.')
        if not re.fullmatch(r'[a-fA-F0-9]{32}', account):
            raise StorageError('R2_ACCOUNT_ID must be the 32-character Cloudflare account id.')
        self.prefix = os.environ.get('R2_PREFIX', 'dolos').strip('/')
        if not re.fullmatch(r'[a-zA-Z0-9/_-]{1,100}', self.prefix) or '..' in self.prefix:
            raise StorageError('R2_PREFIX must be a simple nonempty object-key prefix.')
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise StorageError('Install dolos[r2] to use Cloudflare R2.') from exc
        self.client = boto3.client('s3', endpoint_url=f'https://{account}.r2.cloudflarestorage.com',
                                   aws_access_key_id=access, aws_secret_access_key=secret, region_name='auto',
                                   config=Config(signature_version='s3v4', retries={'max_attempts': 3, 'mode': 'standard'},
                                                 request_checksum_calculation='when_required',
                                                 response_checksum_validation='when_required'))

    def save(self, path, sha256, suffix):
        key = f'{self.prefix}/documents/{sha256}{suffix}'
        with Path(path).open('rb') as body:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body,
                                   ContentLength=Path(path).stat().st_size, ContentType=CONTENT_TYPES[suffix])
        return {'backend': self.backend, 'bucket': self.bucket, 'object_key': key,
                'byte_size': Path(path).stat().st_size}

    def presign(self, sha256, suffix, size, expires=900):
        # Browser writes only staging keys; published document keys are server-only.
        key = f'{self.prefix}/incoming/{uuid.uuid4().hex}/{sha256}{suffix}'
        url = self.client.generate_presigned_url('put_object', Params={
            'Bucket': self.bucket, 'Key': key, 'ContentType': CONTENT_TYPES[suffix], 'ContentLength': size},
            ExpiresIn=expires, HttpMethod='PUT')
        return key, url

    @contextmanager
    def materialize(self, location, sha256, suffix):
        if location is None or location['bucket'] != self.bucket:
            raise StorageError('Document requires its original configured R2 bucket.')
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=location['object_key'])
        except Exception as exc:
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
            if code in {'NoSuchKey', '404', 'NotFound'}:
                raise UploadNotReady('Upload is not present in R2 yet.') from None
            raise StorageError('Could not retrieve the R2 document; check bucket permissions and connectivity.') from None
        body = response['Body']
        try:
            expected = location['byte_size']
            if not 0 < response['ContentLength'] <= MAX_FILE_BYTES or response['ContentLength'] != expected:
                raise ValueError('R2 object size does not match the declared size.')
            with tempfile.TemporaryDirectory(prefix='dolos-document-') as directory:
                path = Path(directory) / ('statement' + suffix)
                total = 0
                with path.open('wb') as output:
                    while True:
                        chunk = body.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > expected or total > MAX_FILE_BYTES:
                            raise ValueError('R2 object exceeds the declared size.')
                        output.write(chunk)
                validate_file(path, sha256, expected)
                yield path
        finally:
            body.close()

    def remove_staging(self, key):
        if not key.startswith(self.prefix + '/incoming/'):
            raise StorageError('Refusing to delete a published document through staging cleanup.')
        self.client.delete_object(Bucket=self.bucket, Key=key)


def storage_for(root, backend=None):
    selected = backend or os.environ.get('DOLOS_STORAGE_BACKEND', 'local')
    if selected == 'local':
        return LocalStorage(root)
    if selected == 'r2':
        return R2Storage()
    raise StorageError('DOLOS_STORAGE_BACKEND must be local or r2.')

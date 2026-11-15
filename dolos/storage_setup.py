"""Provision/check private R2 storage and move existing local documents into it."""
import argparse
import json

from .storage import R2Storage, LocalStorage, StorageError
from .store import Store


def setup_bucket(storage, create=False, origins=None):
    client = storage.client
    try:
        client.head_bucket(Bucket=storage.bucket)
        created = False
    except Exception as exc:
        code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
        if code not in {'404', 'NoSuchBucket', 'NotFound'} or not create:
            raise StorageError('Cannot access R2 bucket. Create it first or use --create-bucket with bucket-admin credentials.') from None
        client.create_bucket(Bucket=storage.bucket)
        created = True
    if origins:
        from urllib.parse import urlsplit
        for origin in origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in {'https', 'http'} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise ValueError('CORS origins must be exact http(s) origins without paths or trailing slashes.')
        # This command owns the CORS configuration; only explicitly supplied
        # origins can upload, and public bucket access is never enabled.
        client.put_bucket_cors(Bucket=storage.bucket, CORSConfiguration={'CORSRules': [{
            'AllowedOrigins': origins, 'AllowedMethods': ['PUT'], 'AllowedHeaders': ['Content-Type'],
            'ExposeHeaders': ['ETag'], 'MaxAgeSeconds': 3600}]})
    # Signed staging URLs can be replayed until expiry; lifecycle removes abandoned
    # or replay-created staging objects without touching published statements.
    try:
        rules = client.get_bucket_lifecycle_configuration(Bucket=storage.bucket).get('Rules', [])
    except Exception as exc:
        code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
        if code not in {'NoSuchLifecycleConfiguration', '404', 'NotFound'}:
            raise StorageError('Could not read R2 lifecycle rules; setup needs bucket-admin permissions.') from None
        rules = []
    rule_id = storage.prefix.replace('/', '-') + '-expire-incoming'
    rules = [rule for rule in rules if rule.get('ID') != rule_id]
    rules.append({'ID': rule_id, 'Status': 'Enabled', 'Filter': {'Prefix': storage.prefix + '/incoming/'},
                  'Expiration': {'Days': 1}})
    client.put_bucket_lifecycle_configuration(Bucket=storage.bucket, LifecycleConfiguration={'Rules': rules})
    return {'bucket': storage.bucket, 'created': created, 'origins': origins or 'unchanged',
            'staging_retention_days': 1, 'public_access': 'not_enabled_by_this_command'}


def migrate_local(store, storage):
    with store.connect() as db:
        documents = [dict(row) for row in db.execute("""SELECT d.* FROM documents d
            LEFT JOIN document_objects o ON o.document_id=d.id
            WHERE d.suffix IN ('.pdf','.csv','.json') AND (o.backend='local' OR o.document_id IS NULL)""")]
    local = LocalStorage(store.root)
    migrated = 0
    for document in documents:
        location = store.object_location(document['id'])
        with local.materialize(location, document['sha256'], document['suffix']) as path:
            replacement = storage.save(path, document['sha256'], document['suffix'])
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT backend FROM document_objects WHERE document_id=?', (document['id'],)).fetchone()
            if current and current['backend'] != 'local':
                continue
            db.execute('DELETE FROM document_objects WHERE document_id=?', (document['id'],))
            store.record_object(db, document['id'], replacement)
        migrated += 1
    return {'migrated': migrated, 'local_files': 'retained'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create-bucket', action='store_true', help='Create a missing bucket; requires bucket-admin credentials')
    parser.add_argument('--origin', action='append', help='Replace CORS with these exact frontend origins; repeat for several')
    parser.add_argument('--migrate-local', action='store_true', help='Copy local statement files to R2 and update their locations')
    parser.add_argument('--check', action='store_true', help='Only check bucket access; leave configuration unchanged')
    args = parser.parse_args()
    storage = R2Storage()
    if args.check:
        if args.create_bucket or args.origin or args.migrate_local:
            parser.error('--check cannot be combined with mutations')
        storage.client.head_bucket(Bucket=storage.bucket)
        result = {'bucket': storage.bucket, 'accessible': True}
    else:
        result = setup_bucket(storage, args.create_bucket, args.origin)
        if args.migrate_local:
            result['backfill'] = migrate_local(Store(), storage)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

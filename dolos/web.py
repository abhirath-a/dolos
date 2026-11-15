"""Serve the built React application and API on one origin."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from .api import app as api


def create_web_app(directory=None):
    web = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    web.mount('/api', api)
    directory = Path(directory) if directory is not None else Path(__file__).parent.parent / 'frontend' / 'dist'
    if (directory / 'index.html').is_file():
        web.mount('/', StaticFiles(directory=directory, html=True), name='frontend')
    else:
        @web.get('/')
        def missing_frontend():
            return JSONResponse({'detail': 'Build the frontend first: cd frontend && pnpm build'}, status_code=503)
    return web


app = create_web_app()

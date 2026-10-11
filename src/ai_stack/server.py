import asyncio
import json
import secrets
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from .engine import Busy, Conflict, Limit


def create_app(engine, port=8765):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_urlsafe(32)
    authority = f'127.0.0.1:{port}'

    @app.middleware('http')
    async def local_boundary(request, call_next):
        if request.headers.get('host') != authority:
            return Response('Invalid host', status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != f'http://{authority}':
            return Response('Invalid origin', status_code=403)
        if request.url.path.startswith('/api/'):
            if not secrets.compare_digest(request.headers.get('x-bakbeat-token', ''), token):
                return Response('Open the local page to access this endpoint.', status_code=403)
        if request.method == 'POST':
            if origin != f'http://{authority}' or request.headers.get('content-type') != 'application/json':
                return Response('Same-origin JSON required', status_code=403)
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > 16000:
                    return Response('Request too large', status_code=413)
            request._body = bytes(content)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get('/')
    def index():
        return HTMLResponse((Path(__file__).parent / 'web/index.html').read_text().replace('__TOKEN__', token))

    @app.get('/app.js')
    def script():
        return Response((Path(__file__).parent / 'web/app.js').read_text(), media_type='text/javascript')

    @app.get('/style.css')
    def style():
        return Response((Path(__file__).parent / 'web/style.css').read_text(), media_type='text/css')

    @app.get('/api/history')
    def history():
        return engine.history()

    @app.get('/api/projects')
    def projects():
        return {'projects': engine.projects()}

    @app.get('/api/conversations')
    def conversations(project_id: str):
        try:
            return {'conversations': engine.conversations(project_id)}
        except ValueError as exc:
            return JSONResponse({'error':str(exc)}, status_code=400)

    @app.get('/api/conversation')
    def conversation(conversation_id: str):
        try:
            return {'tasks': engine.conversation_tasks(conversation_id)}
        except ValueError as exc:
            return JSONResponse({'error':str(exc)}, status_code=400)

    @app.get('/api/task/{task_id}')
    def task(task_id: str):
        result = engine.get(task_id)
        return result if result else JSONResponse({'error':'Request not found.'},status_code=404)

    @app.post('/api/projects')
    async def save_project(request: Request):
        try:
            body=await request.json()
            if not isinstance(body,dict) or set(body)-{'name','notes','id'} or 'name' not in body:
                raise ValueError('Invalid project.')
            return engine.save_project(body['name'],body.get('notes',''),body.get('id'))
        except (ValueError,TypeError):
            return JSONResponse({'error':'Check the project name and notes (8,000 bytes maximum).'},status_code=400)

    @app.post('/api/conversations')
    async def new_conversation(request: Request):
        try:
            body=await request.json()
            if not isinstance(body,dict) or set(body)!={'project_id','title'} or any(not isinstance(v,str) for v in body.values()):
                raise ValueError('Invalid conversation.')
            return engine.create_conversation(body['project_id'],body['title'])
        except ValueError:
            return JSONResponse({'error':'Choose a project and enter a conversation title.'},status_code=400)

    @app.post('/api/context-preview')
    async def preview(request: Request):
        try:
            body=await request.json()
            if not isinstance(body,dict) or set(body)!={'conversation_id','selection','prompt','selected_ids','include_notes'}:
                raise ValueError('Invalid context preview.')
            return engine.preview(**body)
        except (ValueError,Limit) as exc:
            return JSONResponse({'error':str(exc)},status_code=400)

    @app.post('/api/chat')
    async def chat(request: Request):
        try:
            body = await request.json()
            base={'id','selection','prompt'}
            extra={'conversation_id','selected_ids','include_notes','context_hash'}
            if not isinstance(body, dict) or set(body) not in (base,base|extra):
                raise ValueError('Invalid request.')
            if any(not isinstance(body[k],str) for k in base):
                raise ValueError('Invalid request.')
            options={k:body[k] for k in extra if k in body}
            result = await asyncio.to_thread(engine.run, body['id'], body['selection'], body['prompt'], **options)
            return JSONResponse(result)
        except (ValueError, Limit):
            return JSONResponse({'error': 'Request rejected. Check input size, key availability and prototype allowance.'}, status_code=400)
        except Busy:
            return JSONResponse({'error': 'A request is still running. Your saved request is kept; refresh its status or retry it safely.', 'code': 'engine_busy'}, status_code=409)
        except Conflict:
            return JSONResponse({'error': 'Another request is running, the context changed, or this ID was already used. Refresh history and review context before retrying.', 'code': 'request_conflict'}, status_code=409)
        except Exception:
            return JSONResponse({'error': 'Request interrupted. Check history; do not resubmit as a new request until usage is reviewed.'}, status_code=500)

    return app

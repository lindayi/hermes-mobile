"""Private, fail-closed adapter for installed Hermes's authenticated Runs API."""
from urllib.parse import urlparse
import httpx


MAX_NATIVE_RUN_REQUEST_BYTES = 20_000_000


class IntegrationUnavailable(RuntimeError):
    pass


class NativeRunNotFound(IntegrationUnavailable):
    """An authenticated lookup proved that this process no longer owns the run."""


class NativeRunRejected(IntegrationUnavailable):
    """The native handler positively rejected a request before run admission."""


class NativeClarificationRejected(IntegrationUnavailable):
    """The bound native waiter definitively rejected this answer dispatch."""


class GatewayClient:
    def __init__(self, base_url, token, execution_ready=False, transport=None):
        u=urlparse(base_url)
        if u.scheme not in ('http','https') or u.hostname not in ('localhost','127.0.0.1','::1') or u.username or u.password:
            raise ValueError('Hermes endpoint must be a configured loopback address')
        self.execution_ready=execution_ready
        self.token=token
        self.client=httpx.AsyncClient(base_url=base_url.rstrip('/'),headers={'Authorization':'Bearer '+token},timeout=httpx.Timeout(30,read=180),follow_redirects=False,trust_env=False,transport=transport)

    def require_execution(self):
        if not self.token or not self.execution_ready:
            raise IntegrationUnavailable('Agent execution is not enabled: native context/approval/coexistence integration must pass readiness tests first.')

    async def request(self,method,path,**kwargs):
        if not self.token:
            raise IntegrationUnavailable('Private Hermes API is not configured')
        try:
            response=await self.client.request(method,path,**kwargs)
            if (method.upper() == 'POST' and urlparse(path).path == '/v1/runs'
                    and response.status_code == 413):
                raise NativeRunRejected('Photo request was rejected before admission; remove photos or shorten context and resend.')
            segments = urlparse(path).path.split('/')
            if (method.upper() == 'GET' and len(segments) == 4
                    and segments[:3] == ['', 'v1', 'runs'] and segments[3]
                    and response.status_code == 404):
                raise NativeRunNotFound('Native run no longer exists')
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError,ValueError) as exc:
            # Never reflect upstream tokens, stack traces, or arbitrary response bodies.
            raise IntegrationUnavailable('Hermes API unavailable or rejected this operation') from exc

    async def require_action_approvals(self):
        self.require_execution()
        self.action_approvals_ready = False
        result = await self.request('GET', '/v1/capabilities')
        features = result.get('features') if isinstance(result, dict) else None
        if not isinstance(features, dict) or features.get('run_approval_request_id') is not True:
            raise IntegrationUnavailable('Native API cannot atomically bind decisions to this action')
        self.action_approvals_ready = True

    async def approve(self, run_id, request_id, choice):
        from urllib.parse import quote
        if (choice not in ('once', 'deny') or not isinstance(request_id, str)
                or not request_id.strip() or len(request_id) > 200):
            raise ValueError('An exact action identity and once or deny are required')
        await self.require_action_approvals()
        try:
            reply = await self.request('POST', '/v1/runs/' + quote(run_id, safe='') + '/approval',
                                       json={'request_id': request_id, 'choice': choice})
        except IntegrationUnavailable as exc:
            raise IntegrationUnavailable('Approval outcome is unresolved; automatic retry is disabled') from exc
        if (not isinstance(reply, dict) or reply.get('run_id') != run_id
                or reply.get('request_id') != request_id or reply.get('choice') != choice
                or type(reply.get('resolved')) is not int or reply['resolved'] != 1):
            raise IntegrationUnavailable('Approval outcome is unresolved: mismatched acknowledgement; automatic retry is disabled')
        return reply

    async def require_steering(self):
        self.require_execution()
        result = await self.request('GET', '/v1/capabilities')
        controls = result.get('mobile_run_controls') if isinstance(result, dict) else None
        if (not isinstance(controls, dict) or type(controls.get('version')) is not int
                or controls['version'] != 1 or controls.get('steering') is not True
                or controls.get('live_commentary') is not True):
            raise IntegrationUnavailable('Native durable steering controls are unavailable')

    async def require_clarifications(self):
        self.require_execution()
        result = await self.request('GET', '/v1/capabilities')
        controls = result.get('mobile_run_controls') if isinstance(result, dict) else None
        if (not isinstance(controls, dict) or type(controls.get('version')) is not int
                or controls['version'] != 1 or controls.get('clarifications') is not True):
            raise IntegrationUnavailable('Native clarification controls are unavailable')

    async def answer_clarification(self, run_id, question_id, answer, other):
        from urllib.parse import quote
        self.require_execution()
        try:
            response = await self.client.post(
                '/v1/runs/' + quote(run_id, safe='') + '/clarifications/' + quote(question_id, safe=''),
                json={'answer': answer, 'other': other})
            reply = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IntegrationUnavailable('Clarification acknowledgement is unresolved') from exc
        if (response.status_code == 409 and isinstance(reply, dict)
                and set(reply) == {'object', 'run_id', 'question_id', 'status', 'error'}
                and reply.get('object') == 'hermes.run.clarification'
                and reply.get('run_id') == run_id and reply.get('question_id') == question_id
                and reply.get('status') == 'rejected'
                and isinstance(reply.get('error'), dict)
                and set(reply['error']) == {'code'}
                and reply['error'].get('code') in ('clarification_conflict', 'clarification_stale')):
            raise NativeClarificationRejected('Native clarification answer was rejected')
        if (response.status_code != 200 or not isinstance(reply, dict)
                or reply.get('object') != 'hermes.run.clarification'
                or reply.get('run_id') != run_id or reply.get('question_id') != question_id
                or reply.get('status') != 'answered' or reply.get('answer') != answer):
            raise IntegrationUnavailable('Clarification acknowledgement is unresolved')
        return reply

    async def steer(self, run_id, text, key):
        from urllib.parse import quote
        self.require_execution()
        try:
            response = await self.client.post('/v1/runs/' + quote(run_id, safe='') + '/steer',
                                              json={'input': text, 'idempotency_key': key})
            reply = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IntegrationUnavailable('Steering acknowledgement is unresolved') from exc
        if (not isinstance(reply, dict) or reply.get('object') != 'hermes.run.steer'
                or reply.get('run_id') != run_id or reply.get('steer_id') != key or reply.get('idempotency_key', key) != key
                or not ((response.status_code == 200 and reply.get('accepted') is True
                         and reply.get('status') == 'accepted_unconfirmed')
                        or (response.status_code in (200, 409) and reply.get('accepted') is False
                            and reply.get('status') == 'not_delivered')
                        or (response.status_code == 200 and type(reply.get('accepted')) is bool
                            and reply.get('status') == 'unknown'))):
            raise IntegrationUnavailable('Steering acknowledgement is unresolved')
        return reply

    async def start(self,session_id,text,history=None,*,model=None,provider=None,
                    attachments=None,attachment_ids=None):
        self.require_execution()
        payload=self._run_payload(session_id,text,history,model=model,provider=provider,
                                  attachments=attachments,attachment_ids=attachment_ids)
        request=self.client.build_request('POST','/v1/runs',json=payload)
        if len(request.content)>MAX_NATIVE_RUN_REQUEST_BYTES:
            raise ValueError('Photo request exceeds the native handler limit; remove photos or shorten earlier context.')
        if attachments:
            try:
                capabilities = await self.request('GET', '/v1/capabilities')
            except IntegrationUnavailable:
                raise NativeRunRejected('Native photo capability could not be verified; no image request was dispatched.') from None
            photos = capabilities.get('mobile_photos') if isinstance(capabilities, dict) else None
            if photos != {'version': 1, 'max_images': 4, 'max_image_bytes': 2 * 1024 * 1024,
                          'max_request_bytes': MAX_NATIVE_RUN_REQUEST_BYTES, 'private_persistence': True}:
                raise NativeRunRejected('Native photos are unavailable; activate the reviewed photo listener before resending.')
        return await self.request('POST','/v1/runs',content=request.content,
                                  headers={'Content-Type':'application/json'})

    @staticmethod
    def _run_payload(session_id,text,history=None,*,model=None,provider=None,
                     attachments=None,attachment_ids=None):
        payload={'session_id':session_id,'input':text,'conversation_history':history or []}
        if attachments is not None or attachment_ids is not None:
            if (not isinstance(attachments,list) or not 1 <= len(attachments) <= 4
                    or not isinstance(attachment_ids,list) or len(attachment_ids)!=len(attachments)
                    or any(not isinstance(value,dict) or value.get('type')!='image_url'
                           or not isinstance(value.get('image_url'),dict)
                           or not isinstance(value['image_url'].get('url'),str)
                           or not value['image_url']['url'].startswith('data:image/')
                           for value in attachments)
                    or any(not isinstance(value,str) or len(value)!=32
                           or any(char not in '0123456789abcdef' for char in value)
                           for value in attachment_ids)):
                raise ValueError('Invalid private photo run binding')
            payload['input']=[{'role':'user','content':[
                {'type':'text','text':text}, *attachments]}]
            payload['mobile_attachment_ids']=attachment_ids
        if model is not None or provider is not None:
            if not model or not provider:
                raise ValueError('Both model and provider are required')
            payload.update(model=model,provider=provider)
        return payload

    def validate_run_size(self,session_id,text,history,attachment_ids,image_sizes,*,model=None,provider=None):
        attachments=[{'type':'image_url','image_url':{
            'url':f'data:{content_type};base64,'}} for content_type,_ in image_sizes]
        payload=self._run_payload(session_id,text,history,model=model,provider=provider,
                                  attachments=attachments,attachment_ids=attachment_ids)
        body=self.client.build_request('POST','/v1/runs',json=payload).content
        size=len(body)+sum(((image_size+2)//3)*4 for _,image_size in image_sizes)
        if size>MAX_NATIVE_RUN_REQUEST_BYTES:
            raise ValueError('Photo request exceeds the native handler limit; remove photos or shorten earlier context.')
        return size

    async def stop(self,run_id):
        from urllib.parse import quote
        self.require_execution()
        return await self.request('POST','/v1/runs/'+quote(run_id,safe='')+'/stop')

    async def events(self,run_id):
        from urllib.parse import quote
        import json
        self.require_execution()
        name='message'
        data=[]
        size=0
        try:
            async with self.client.stream('GET','/v1/runs/'+quote(run_id,safe='')+'/events') as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    size+=len(line)
                    if size > 1048576:
                        raise IntegrationUnavailable('Upstream event exceeded size limit')
                    if not line:
                        if data:
                            value=json.loads('\n'.join(data))
                            if not isinstance(value,dict):
                                raise IntegrationUnavailable('Invalid upstream event')
                            yield dict(value,event=value.get('event',name))
                        name,data,size='message',[],0
                    elif line.startswith('event:'):
                        name=line[6:].strip()
                    elif line.startswith('data:'):
                        data.append(line[5:].strip())
        except (httpx.HTTPError,ValueError) as exc:
            raise IntegrationUnavailable('Hermes event stream interrupted') from exc

    async def history(self,profile,session_id):
        # Server-selected GatewayClient is profile-scoped; callers cannot choose its token or URL.
        from urllib.parse import quote
        import json
        history=[]
        canonical_id = None
        for offset in range(0,100000,500):
            result=await self.request('GET','/api/sessions/'+quote(session_id,safe='')+'/messages',params={'limit':500,'offset':offset,'order':'oldest'})
            if not isinstance(result, dict):
                raise IntegrationUnavailable('Invalid native history response')
            resolved_id = result.get('session_id')
            if (not isinstance(resolved_id, str) or not resolved_id.strip() or len(resolved_id) > 200
                    or (canonical_id is not None and resolved_id != canonical_id)
                    or (resolved_id != session_id and result.get('requested_session_id') != session_id)
                    or not isinstance(result.get('data'), list)):
                raise IntegrationUnavailable('Native session changed; refresh its lineage before continuing')
            canonical_id = resolved_id
            rows=result['data']
            for row in rows:
                if not isinstance(row,dict) or row.get('role') not in ('user','assistant','tool','system'):
                    raise IntegrationUnavailable('Invalid native conversation record')
                msg={'role':row['role'],'content':row.get('content')}
                if row.get('tool_calls'):
                    try:
                        calls=json.loads(row['tool_calls']) if isinstance(row['tool_calls'],str) else row['tool_calls']
                    except ValueError as exc:
                        raise IntegrationUnavailable('Native tool history cannot be decoded') from exc
                    if not isinstance(calls,list):
                        raise IntegrationUnavailable('Invalid tool history')
                    msg['tool_calls']=calls
                if row.get('tool_call_id'):
                    msg['tool_call_id']=row['tool_call_id']
                history.append(msg)
            if len(rows)<500:
                return {'complete':True,'profile':profile,'session_id':session_id,'canonical_session_id':canonical_id,'history':history}
        raise IntegrationUnavailable('History exceeds safe import bound; cannot silently truncate context')

    async def close(self):
        await self.client.aclose()

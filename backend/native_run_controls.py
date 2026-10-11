"""Process-local compatibility for the dedicated mobile owner listener only."""
import json
import base64
import inspect
import re
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps

from aiohttp import web


PHOTO_REQUEST_BYTES = 20_000_000
TEXT_REQUEST_BYTES = 10_000_000
PHOTO_OMITTED = '[screenshot] [photo attachment omitted after processing]'
_INLINE_PHOTO = re.compile(r'data:image/[^;,\s"\\]+;base64,[A-Za-z0-9+/=_-]*', re.I)
_MOBILE_PHOTO_HOOK_ACTIVE = ContextVar('mobile_photo_hook_active', default=False)
_PHOTO_HOOK_INSTALL_LOCK = threading.Lock()


class NativePhotoFailure(RuntimeError):
    status_code = 422


def photo_persistence_copy(value):
    """Copy only at retention boundaries; live provider input is never changed."""
    if isinstance(value, str):
        return _INLINE_PHOTO.sub(PHOTO_OMITTED, value)
    if isinstance(value, list):
        return [photo_persistence_copy(item) for item in value]
    if isinstance(value, dict):
        if value.get('type') in {'image_url', 'input_image', 'image'}:
            return {'type': 'text', 'text': PHOTO_OMITTED}
        return {key: (None if key == 'api_content' and _INLINE_PHOTO.search(str(item))
                      else photo_persistence_copy(item)) for key, item in value.items()}
    return value


def _compact_utf8_size(value):
    try:
        return len(json.dumps(
            value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    except UnicodeEncodeError:
        raise ValueError('Native text request is not valid UTF-8') from None


def validate_photo_text_budget(body):
    # Called after native binding validation, or with the bridge's generated
    # current-turn placeholders. Only these URL values get the image budget;
    # persistence redaction must never hide ordinary text or extra metadata.
    content = body['input'][0]['content']
    counted = dict(body, input=[dict(body['input'][0], content=[
        dict(part, image_url=dict(part['image_url'], url=PHOTO_OMITTED))
        if isinstance(part, dict) and part.get('type') == 'image_url' else part
        for part in content])])
    if _compact_utf8_size(counted) > TEXT_REQUEST_BYTES:
        raise ValueError('Native text request exceeds the existing limit')


def _canonical_photo_content(content):
    if not isinstance(content, list):
        return photo_persistence_copy(content)
    image_count = sum(isinstance(part, dict)
                      and part.get('type') in {'image_url', 'input_image', 'image'}
                      for part in content)
    if not image_count or any(not isinstance(part, dict)
                              or part.get('type') not in {'text', 'image_url', 'input_image', 'image'}
                              for part in content):
        return photo_persistence_copy(content)
    text = '\n'.join(part['text'] for part in content
                     if part.get('type') == 'text' and isinstance(part.get('text'), str))
    return text + '\n[screenshot]' * image_count if text else '[screenshot]' * image_count


def _canonical_photo_message(value):
    if isinstance(value, list):
        return [_canonical_photo_message(item) for item in value]
    if not isinstance(value, dict):
        return photo_persistence_copy(value)
    copied = {key: (None if key == 'api_content' and _INLINE_PHOTO.search(str(item))
                    else _canonical_photo_message(item))
              for key, item in value.items()}
    if value.get('role') == 'user' and isinstance(value.get('content'), list):
        copied['content'] = _canonical_photo_content(value['content'])
    return copied


def _install_photo_log_filters():
    import logging

    class PhotoLogFilter(logging.Filter):
        _mobile_photo_log_filter = True

        def filter(self, record):
            try:
                message = record.getMessage()
            except Exception:
                return True
            sanitized = photo_persistence_copy(message)
            if sanitized != message:
                record.msg, record.args = sanitized, ()
            error = record.exc_info[1] if record.exc_info else None
            seen = set()
            while error is not None and id(error) not in seen:
                seen.add(id(error))
                if _INLINE_PHOTO.search(str(error)):
                    record.exc_info = record.exc_text = None
                    break
                error = error.__cause__ or error.__context__
            return True

    for name in ('agent.conversation_loop', 'gateway.platforms.api_server'):
        logger = logging.getLogger(name)
        if not any(getattr(item, '_mobile_photo_log_filter', False) for item in logger.filters):
            logger.addFilter(PhotoLogFilter())


def validate_photo_payload(body):
    if not isinstance(body, dict):
        raise ValueError('Expected a JSON object')
    ids = body.get('mobile_attachment_ids')
    image_types = {'image_url', 'input_image', 'image'}

    def image_parts(value):
        pending = [value]
        found = []
        while pending:
            current = pending.pop()
            if isinstance(current, list):
                pending.extend(current)
            elif isinstance(current, dict):
                kind = current.get('type')
                if isinstance(kind, str) and kind in image_types:
                    found.append(current)
                pending.extend(current.values())
        return found

    if ids is None:
        if image_parts(body):
            raise ValueError('Private photo input requires attachment binding')
        if _compact_utf8_size(body) > TEXT_REQUEST_BYTES:
            raise ValueError('Native text request exceeds the existing limit')
        return
    content = body.get('input')
    if (not isinstance(content, list) or len(content) != 1
            or not isinstance(content[0], dict) or content[0].get('role') != 'user'
            or not isinstance(content[0].get('content'), list)):
        raise ValueError('Invalid private photo input')
    images = [part for part in content[0]['content']
              if isinstance(part, dict) and part.get('type') == 'image_url']
    all_images = image_parts(body)
    allowed_images = {id(part) for part in images}
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 4
            or any(not isinstance(item, str) or not re.fullmatch('[0-9a-f]{32}', item)
                   for item in ids)
            or len(set(ids)) != len(ids) or len(images) != len(ids)
            or len(all_images) != len(images)
            or any(id(part) not in allowed_images for part in all_images)):
        raise ValueError('Invalid private photo binding')
    for part in images:
        image = part.get('image_url')
        url = image.get('url') if isinstance(image, dict) else None
        if not isinstance(url, str) or not re.fullmatch(
                r'data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/]+={0,2}', url):
            raise ValueError('Only normalized inline photos are allowed')
        try:
            size = len(base64.b64decode(url.split(',', 1)[1], validate=True))
        except ValueError:
            raise ValueError('Invalid photo encoding') from None
        if not 0 < size <= 2 * 1024 * 1024:
            raise ValueError('Photo exceeds the normalized image limit')
    validate_photo_text_budget(body)


def _install_photo_hook_privacy():
    import hermes_cli.lifecycle as lifecycle

    with _PHOTO_HOOK_INSTALL_LOCK:
        original = lifecycle.invoke_hook
        if getattr(original, '_mobile_photo_hook_privacy', False):
            return

        @wraps(original)
        def invoke_hook(name, *args, **kwargs):
            if _MOBILE_PHOTO_HOOK_ACTIVE.get():
                args = tuple(photo_persistence_copy(item) for item in args)
                kwargs = photo_persistence_copy(kwargs)
            return original(name, *args, **kwargs)

        invoke_hook._mobile_photo_hook_privacy = True
        lifecycle.invoke_hook = invoke_hook


@lru_cache(maxsize=16)
def _private_photo_class(base):
    class PrivatePhotoAgent(base):
        def __setattr__(self, name, value):
            if name == '_vision_supported' and value is False and getattr(self, '_mobile_photo_turn', False):
                super().__setattr__('_mobile_photo_failed', True)
                raise NativePhotoFailure('The configured model rejected photo input; select a vision-capable model and resend.')
            super().__setattr__(name, value)

        def run_conversation(self, user_message, *args, **kwargs):
            self._mobile_photo_turn = isinstance(user_message, list) and any(
                isinstance(part, dict) and part.get('type') == 'image_url' for part in user_message)
            self._mobile_photo_failed = False
            if self._mobile_photo_turn and not getattr(self, '_vision_supported', True):
                self._mobile_photo_turn = False
                raise NativePhotoFailure('The configured model does not support photos; select a vision-capable model and resend.')
            _install_photo_hook_privacy()
            hook_token = _MOBILE_PHOTO_HOOK_ACTIVE.set(self._mobile_photo_turn)
            try:
                try:
                    result = super().run_conversation(user_message, *args, **kwargs)
                except Exception:
                    if self._mobile_photo_turn:
                        raise NativePhotoFailure('The photo run failed; check the configured model and retry.') from None
                    raise
                if self._mobile_photo_turn and getattr(self, '_mobile_photo_failed', False):
                    raise NativePhotoFailure('The configured vision analyzer could not process the attached image.')
                if self._mobile_photo_turn:
                    return photo_persistence_copy(result)
                return result
            finally:
                _MOBILE_PHOTO_HOOK_ACTIVE.reset(hook_token)
                self._mobile_photo_turn = False
                self._mobile_photo_failed = False

        def _describe_image_for_anthropic_fallback(self, image_url, role):
            if getattr(self, '_mobile_photo_turn', False):
                self._mobile_photo_failed = True
                raise NativePhotoFailure('The configured vision analyzer could not process the attached image.')
            return super()._describe_image_for_anthropic_fallback(image_url, role)

        def _save_session_log(self, messages=None):
            return super()._save_session_log(photo_persistence_copy(
                messages if messages is not None else self._session_messages))

        def _dump_api_request_debug(self, api_kwargs, *, reason, error=None):
            safe_error = RuntimeError(photo_persistence_copy(str(error))) if error is not None else None
            return super()._dump_api_request_debug(photo_persistence_copy(api_kwargs),
                                                  reason=reason, error=safe_error)

        def _spawn_background_review(self, messages_snapshot, *args, **kwargs):
            # The fork uses a plain native agent, not this persistence wrapper.
            # Sanitize its input before the asynchronous handoff without
            # mutating the foreground messages needed by the vision model.
            return super()._spawn_background_review(
                photo_persistence_copy(messages_snapshot), *args, **kwargs)

        def _convert_to_trajectory_format(self, messages, user_query, completed):
            return super()._convert_to_trajectory_format(
                photo_persistence_copy(messages), photo_persistence_copy(user_query), completed)

        def _api_request_payload_for_hook(self, api_kwargs):
            return photo_persistence_copy(super()._api_request_payload_for_hook(api_kwargs))

        def _invoke_api_request_error_hook(self, **kwargs):
            return super()._invoke_api_request_error_hook(**photo_persistence_copy(kwargs))

        def _vprint(self, *args, **kwargs):
            return super()._vprint(*(photo_persistence_copy(arg) for arg in args), **kwargs)

        def _emit_status(self, message):
            return super()._emit_status(photo_persistence_copy(message))

        def _buffer_vprint(self, message):
            return super()._buffer_vprint(photo_persistence_copy(message))

        def _clean_error_message(self, error_msg):
            return super()._clean_error_message(photo_persistence_copy(error_msg))

    return PrivatePhotoAgent


def _private_db_method(method):
    signature = inspect.signature(method)
    def private_write(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        for key in ('messages', 'content', 'api_content'):
            if key in bound.arguments:
                value = bound.arguments[key]
                if key == 'api_content' and _INLINE_PHOTO.search(str(value)):
                    bound.arguments[key] = None
                elif key == 'content' and bound.arguments.get('role') == 'user':
                    bound.arguments[key] = _canonical_photo_content(value)
                elif key == 'messages':
                    bound.arguments[key] = _canonical_photo_message(value)
                elif key == 'content':
                    bound.arguments[key] = photo_persistence_copy(value)
                else:
                    bound.arguments[key] = photo_persistence_copy(value)
        return method(*bound.args, **bound.kwargs)
    return private_write


def private_photo_agent(agent):
    _install_photo_log_filters()
    agent.__class__ = _private_photo_class(type(agent))
    db = getattr(agent, '_session_db', None)
    if db is not None and not getattr(db, '_mobile_photo_private', False):
        for name in ('_insert_message_rows', 'append_message', 'archive_and_compact',
                     'set_latest_user_api_content'):
            setattr(db, name, _private_db_method(getattr(db, name)))
        db._mobile_photo_private = True
    return agent


def run_controls_adapter(base):
    class RunControlsAdapter(base):
        async def connect(self, **kwargs):
            # This entrypoint is a dedicated listener, not the shared gateway.
            from gateway.platforms import api_server
            api_server.MAX_REQUEST_BYTES = PHOTO_REQUEST_BYTES
            original_limit = api_server.body_limit_middleware
            if not getattr(original_limit, '_mobile_photo_limit', False):
                @web.middleware
                async def body_limit(request, handler):
                    limit = (PHOTO_REQUEST_BYTES if request.path.endswith('/v1/runs')
                             else TEXT_REQUEST_BYTES)
                    length = request.headers.get('Content-Length')
                    if length is not None:
                        try:
                            if int(length) > limit:
                                return web.json_response({'error': {'code': 'body_too_large'}}, status=413)
                        except ValueError:
                            return web.json_response({'error': {'code': 'invalid_content_length'}}, status=400)
                    return await original_limit(request.clone(client_max_size=limit), handler)
                body_limit._mobile_photo_limit = True
                api_server.body_limit_middleware = body_limit
            # Keep parsing requests visible to the native drain fence too.
            if not getattr(type(self), '_photo_admission_wrapped', False):
                type(self)._handle_runs = api_server._admit_api_agent_request(type(self)._handle_runs)
                type(self)._photo_admission_wrapped = True
            return await super().connect(**kwargs)

        async def _handle_runs(self, request):
            auth = self._check_auth(request)
            if auth is not None:
                return auth
            try:
                body = await request.json()
                validate_photo_payload(body)
            except web.HTTPRequestEntityTooLarge:
                return web.json_response({'error': {'code': 'body_too_large'}}, status=413)
            except (ValueError, TypeError):
                return web.json_response({'error': {'code': 'photo_input_rejected',
                    'message': 'Photo payload is invalid or exceeds the photo/text limits.'}}, status=413)
            native = super()._handle_runs
            if getattr(type(self), '_photo_admission_wrapped', False) and hasattr(native, '__wrapped__'):
                return await native.__wrapped__(self, request)
            return await native(request)

        def __init__(self, *args, **kwargs):
            self._controls_lock = threading.RLock()
            self._controls = {}
            super().__init__(*args, **kwargs)

        def _state(self, run_id):
            return self._controls.setdefault(run_id, {
                'receipts': {}, 'closed': False, 'pending': [], 'clarifications': {}})

        @staticmethod
        def _clarification_view(item):
            return {key: item[key] for key in (
                'question_id', 'question', 'choices', 'multi_select', 'status',
                'answer', 'other', 'created_at', 'updated_at') if key in item}

        def _clarification_snapshot(self, state):
            return [self._clarification_view(item)
                    for item in state.get('clarifications', {}).values()]

        def _release_clarifications(self, run_id, state, status):
            changed = []
            for item in state.get('clarifications', {}).values():
                if item['status'] == 'pending':
                    item.update(status=status, updated_at=time.time())
                    item['signal'].set()
                    changed.append(item)
            for item in changed:
                state['emit']({'event': 'run.clarification', 'run_id': run_id,
                               **self._clarification_view(item)})

        def _sweep_orphaned_runs_once(self, now=None):
            with self._controls_lock:
                super()._sweep_orphaned_runs_once(now)
                for run_id in list(self._controls):
                    if run_id not in self._run_statuses:
                        del self._controls[run_id]

        def _retain(self, state, text):
            pending = state.setdefault('pending', [])
            if isinstance(text, str) and text and text not in pending and text != '\n'.join(pending):
                pending.append(text)

        def _snapshot(self, run_id):
            state = self._state(run_id)
            fields = {'steer_receipts': [dict(v['receipt']) for v in state['receipts'].values()]}
            if state.get('pending'):
                fields['pending_steer'] = '\n'.join(state['pending'])
            if state.get('clarifications'):
                fields['clarifications'] = self._clarification_snapshot(state)
            return fields

        def _set_run_status(self, run_id, status, **fields):
            with self._controls_lock:
                state = self._controls.get(run_id)
                current = self._run_statuses.get(run_id, {})
                previous = (state.get('terminal_status') if state else None) or current.get('status')
                terminal = {'completed', 'failed', 'cancelled'}
                if (previous in terminal and status != previous
                        or previous == 'stopping' and status not in terminal | {'stopping'}):
                    return current
                if state and status in {'completed', 'failed', 'cancelled', 'stopping'}:
                    state['closed'] = True
                    self._release_clarifications(
                        run_id, state, 'cancelled' if status in {'stopping', 'cancelled'} else 'expired')
                    agent = self._active_run_agents.get(run_id)
                    if agent is not None:
                        self._retain(state, agent._drain_pending_steer() if hasattr(agent, '_drain_pending_steer') else None)
                    self._retain(state, fields.get('pending_steer'))
                    for item in state['receipts'].values():
                        if item['receipt']['status'] == 'accepted_unconfirmed':
                            item['receipt']['status'] = 'unknown'
                    fields.update(self._snapshot(run_id))
                    emit = state.get('emit')
                    if emit:
                        emit({'event': 'run.steer_receipts', 'run_id': run_id, **fields})
                return super()._set_run_status(run_id, status, **fields)

        def _make_run_event_callback(self, run_id, loop):
            native_callback = super()._make_run_event_callback(run_id, loop)
            with self._controls_lock:
                state = self._state(run_id)
                q = self._run_streams.get(run_id)
                state['queue'] = q
                state['approval_session'] = getattr(self, '_run_approval_sessions', {}).get(run_id)
            def owns_run():
                return (self._controls.get(run_id) is state
                        and self._run_streams.get(run_id) is q)
            def callback(*args, **kwargs):
                # Lock the native status READ as well as its eventual update.
                # Identity fences old workers without permanent tombstones.
                with self._controls_lock:
                    if not owns_run() or state['closed']:
                        return
                    return native_callback(*args, **kwargs)
            callback._mobile_run_id = run_id
            if q is not None:
                native_put = q.put_nowait
                def put_with_controls(event):
                    with self._controls_lock:
                        if not owns_run():
                            return
                        # Native terminal frames can precede status publication.
                        if isinstance(event, dict) and event.get('event') in {
                                'run.completed', 'run.failed', 'run.cancelled'}:
                            if state.get('terminal_status'):
                                return
                            status = event['event'].split('.')[1]
                            published = self._run_statuses.get(run_id, {}).get('status')
                            if published in {'completed', 'failed', 'cancelled'} and published != status:
                                return
                            state['terminal_status'] = status
                            state['closed'] = True
                            self._release_clarifications(
                                run_id, state, 'cancelled' if status == 'cancelled' else 'expired')
                            agent = self._active_run_agents.get(run_id)
                            if agent is not None and hasattr(agent, '_drain_pending_steer'):
                                self._retain(state, agent._drain_pending_steer())
                            self._retain(state, event.get('pending_steer'))
                            for item in state['receipts'].values():
                                if item['receipt']['status'] == 'accepted_unconfirmed':
                                    item['receipt']['status'] = 'unknown'
                            event = {**event, **self._snapshot(run_id)}
                        native_put(event)
                q.put_nowait = put_with_controls
            def emit(event):
                def put():
                    if q is not None:
                        q.put_nowait(event)
                try:
                    loop.call_soon_threadsafe(put)
                except RuntimeError:
                    pass  # Closed loop; pollable receipt remains authoritative.
            state['emit'] = emit
            return callback

        def _create_agent(self, *args, **kwargs):
            agent = super()._create_agent(*args, **kwargs)
            if hasattr(agent, '_save_session_log'):
                agent = private_photo_agent(agent)
            run_id = getattr(kwargs.get('tool_progress_callback'), '_mobile_run_id', None)
            if run_id is None:
                return agent  # Other native entry points retain their ordinary lifecycle.
            state = self._state(run_id)
            state['agent'] = agent
            original_steer = agent.steer
            original_run = agent.run_conversation
            original_clear = agent.clear_interrupt
            def steer(text):
                with self._controls_lock:
                    return False if state['closed'] else original_steer(text)
            def clear_interrupt(*a, **kw):
                with self._controls_lock:
                    with agent._pending_steer_lock:
                        pending = agent._pending_steer
                    cleared = original_clear(*a, **kw)
                    if cleared:
                        self._retain(state, pending)
                    return cleared
            def run(*a, **kw):
                result = None
                try:
                    result = original_run(*a, **kw)
                    return result
                finally:
                    with self._controls_lock:
                        state['closed'] = True
                        self._release_clarifications(run_id, state, 'cancelled')
                        if isinstance(result, dict):
                            self._retain(state, result.get('pending_steer'))
                        self._retain(state, agent._drain_pending_steer())
                        if isinstance(result, dict) and state.get('pending'):
                            result['pending_steer'] = '\n'.join(state['pending'])
            agent.steer = steer
            agent.clear_interrupt = clear_interrupt
            agent.run_conversation = run
            agent.clarify_callback = lambda question, choices=None, multi_select=False: (
                self._clarify(run_id, agent, question, choices, multi_select))
            def commentary(text, already_streamed=False):
                # This is the native safe PUBLIC interim seam, never reasoning.
                if (already_streamed or not getattr(agent, 'show_commentary', True)
                        or not isinstance(text, str) or not text.strip()):
                    return
                with self._controls_lock:
                    if not state['closed']:
                        state['emit']({'event': 'message.commentary', 'run_id': run_id,
                                       'timestamp': time.time(), 'text': text,
                                       'phase': 'commentary', 'channel': 'commentary'})
            agent.interim_assistant_callback = commentary
            return agent

        def _clarify(self, run_id, agent, question, choices=None, multi_select=False):
            if (not isinstance(question, str) or not question.strip() or len(question) > 4096
                    or type(multi_select) is not bool):
                raise ValueError('Invalid clarification request')
            if choices is not None and (
                    not isinstance(choices, list) or len(choices) > 4
                    or any(not isinstance(choice, str) or not choice.strip() or len(choice) > 512
                           for choice in choices)):
                raise ValueError('Invalid clarification choices')
            choices = list(choices) if choices else None
            run_id = str(run_id)
            with self._controls_lock:
                state = self._controls.get(run_id)
                current = self._run_statuses.get(run_id, {})
                if (state is None or state['closed'] or state.get('agent') is not agent
                        or current.get('status') not in {'running', 'waiting_for_clarification'}):
                    raise RuntimeError('Clarification is unavailable for this run.')
                records = state.setdefault('clarifications', {})
                if len(records) >= 256 or any(item['status'] == 'pending' for item in records.values()):
                    raise RuntimeError('Clarification capacity is unavailable.')
                now = time.time()
                item = {
                    'question_id': uuid.uuid4().hex, 'question': question.strip(),
                    'choices': choices, 'multi_select': multi_select, 'status': 'pending',
                    'created_at': now, 'updated_at': now, 'signal': threading.Event(),
                }
                records[item['question_id']] = item
                self._set_run_status(run_id, 'waiting_for_clarification')
                state['emit']({'event': 'run.clarification', 'run_id': run_id,
                               **self._clarification_view(item)})
            configured = getattr(agent, 'clarify_timeout', 3600)
            timeout = (min(float(configured), 3600)
                       if type(configured) in (int, float) and configured > 0 else 3600)
            if not item['signal'].wait(timeout):
                with self._controls_lock:
                    if item['status'] == 'pending':
                        item.update(status='expired', updated_at=time.time())
                        if not any(entry['status'] == 'pending' for entry in records.values()):
                            self._set_run_status(run_id, 'running')
                        state['emit']({'event': 'run.clarification', 'run_id': run_id,
                                       **self._clarification_view(item)})
            with self._controls_lock:
                if item['status'] != 'answered':
                    raise RuntimeError(f"Clarification {item['status']}.")
                return item['answer']

        def _validate_clarification_answer(self, item, body):
            if not isinstance(body, dict) or set(body) != {'answer', 'other'} or type(body.get('other')) is not bool:
                return None
            answer, other = body['answer'], body['other']
            choices = item['choices']
            if item['multi_select'] and choices is not None:
                if (not isinstance(answer, list) or not answer or len(answer) > 5
                        or any(not isinstance(value, str) or not value.strip() or len(value) > 32768
                               for value in answer)):
                    return None
                if len(answer) != len(set(answer)):
                    return None
                unknown = [value for value in answer if value not in choices]
                if (unknown and (not other or len(unknown) != 1 or answer[-1] != unknown[0])
                        or other and not unknown):
                    return None
                return answer
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 32768:
                return None
            if choices is not None:
                if other:
                    if answer in choices:
                        return None
                elif answer not in choices:
                    return None
            elif other:
                return None
            return answer

        async def _handle_clarification_answer(self, request):
            error = self._check_auth(request)
            if error is not None:
                return error
            body, error = await self._read_json_body(request)
            if error is not None:
                return error
            run_id = request.match_info['run_id']
            question_id = request.match_info['question_id']
            with self._controls_lock:
                state = self._controls.get(run_id)
                item = state.get('clarifications', {}).get(question_id) if state else None
                if item is None:
                    return web.json_response({'error': {'code': 'clarification_not_found'}}, status=404)
                answer = self._validate_clarification_answer(item, body)
                if answer is None:
                    return web.json_response({'error': {'code': 'invalid_clarification_answer'}}, status=400)
                if item['status'] != 'pending':
                    if (item['status'] == 'answered' and item['answer'] == answer
                            and item['other'] == body['other']):
                        return web.json_response({
                            'object': 'hermes.run.clarification', 'run_id': run_id,
                            'question_id': question_id, 'status': 'answered', 'answer': item['answer']})
                    return web.json_response({
                        'object': 'hermes.run.clarification', 'run_id': run_id,
                        'question_id': question_id, 'status': 'rejected',
                        'error': {'code': 'clarification_conflict'}}, status=409)
                if (state['closed'] or self._run_statuses.get(run_id, {}).get('status')
                        != 'waiting_for_clarification'):
                    return web.json_response({
                        'object': 'hermes.run.clarification', 'run_id': run_id,
                        'question_id': question_id, 'status': 'rejected',
                        'error': {'code': 'clarification_stale'}}, status=409)
                item.update(status='answered', answer=answer, other=body['other'], updated_at=time.time())
                item['signal'].set()
                self._set_run_status(run_id, 'running')
                state['emit']({'event': 'run.clarification', 'run_id': run_id,
                               **self._clarification_view(item)})
                return web.json_response({
                    'object': 'hermes.run.clarification', 'run_id': run_id,
                    'question_id': question_id, 'status': 'answered', 'answer': answer})

        def _http_route_table(self):
            routes = super()._http_route_table()
            return [*routes, ('POST', '/v1/runs/{run_id}/clarifications/{question_id}',
                              self._handle_clarification_answer)]

        async def _handle_steer_run(self, request):
            error = self._check_auth(request)
            if error is not None:
                return error
            body, error = await self._read_json_body(request)
            if error is not None:
                return error
            if (not isinstance(body, dict) or set(body) != {'input', 'idempotency_key'}
                    or not isinstance(body.get('input'), str)
                    or not 0 < len(body['input']) <= 32768 or not body['input'].strip()
                    or not isinstance(body.get('idempotency_key'), str)
                    or not 0 < len(body['idempotency_key']) <= 128
                    or not body['idempotency_key'].strip()):
                return web.json_response({'error': {'code': 'invalid_steer_input'}}, status=400)
            run_id, key, text = request.match_info['run_id'], body['idempotency_key'], body['input']
            with self._controls_lock, self._approval_snapshot(run_id) as pending:
                if run_id not in self._run_statuses:
                    return web.json_response({'error': {'code': 'run_not_found'}}, status=404)
                state = self._controls.setdefault(run_id, {'receipts': {}, 'closed': False})
                previous = state['receipts'].get(key)
                if previous:
                    if previous['input'] != text:
                        return web.json_response({'error': {'code': 'idempotency_conflict'}}, status=409)
                    return web.json_response(previous['receipt'])
                if len(state['receipts']) >= 256:
                    return web.json_response({'error': {'code': 'steer_capacity'}}, status=429)
                agent = self._active_run_agents.get(run_id)
                eligible = (self._run_statuses[run_id].get('status') == 'running'
                            and pending == []
                            and state.get('approval_session') == run_id
                            == getattr(self, '_run_approval_sessions', {}).get(run_id)
                            and run_id not in self._stopping_run_ids and not state['closed']
                            and callable(getattr(agent, 'steer', None)))
                receipt = {'object': 'hermes.run.steer', 'run_id': run_id,
                           'steer_id': key, 'status': 'not_delivered', 'accepted': False}
                state['receipts'][key] = {'input': text, 'receipt': receipt}
                if eligible:
                    # Record before calling: an exception may happen after acceptance.
                    receipt['status'] = 'unknown'
                    try:
                        receipt['accepted'] = bool(agent.steer(text))
                        receipt['status'] = 'accepted_unconfirmed' if receipt['accepted'] else 'not_delivered'
                    except Exception:
                        pass
                self._run_statuses[run_id]['steer_receipts'] = [
                    dict(item['receipt']) for item in state['receipts'].values()]
                return web.json_response(receipt, status=200 if eligible else 409)

        @contextmanager
        def _approval_snapshot(self, run_id):
            """Caller holds controls; retain registry lock through publication/admission.

            Native inserts/drops entries under approval._lock, but calls notify
            AFTER releasing it. Order is controls -> approval; never call the
            public listing API under its non-reentrant lock.
            """
            pending, locked, lock = None, False, None
            state = self._controls.get(run_id)
            current = self._run_statuses.get(run_id)
            task = getattr(self, '_active_run_tasks', {}).get(run_id)
            agent = self._active_run_agents.get(run_id)
            session = getattr(self, '_run_approval_sessions', {}).get(run_id)
            try:
                if not isinstance(session, str) or not session.strip():
                    raise ValueError('Missing approval identity')
                from tools import approval
                listed = approval.list_gateway_approvals(session)
                lock = approval._lock
                lock.acquire()
                locked = True
                queues = approval._gateway_queues
                if not isinstance(queues, dict):
                    raise ValueError('Unavailable approval registry')
                entries = queues.get(session, [])
                if not isinstance(entries, list):
                    raise ValueError('Malformed approval queue')
                fresh = [dict(entry.data) for entry in entries]
                if (not isinstance(listed, list) or listed != fresh or len(fresh) > 1000
                        or any(not isinstance(item.get('request_id'), str)
                               or not 0 < len(item['request_id']) <= 200 for item in fresh)
                        or self._run_approval_sessions.get(run_id) != session):
                    raise ValueError('Changed or malformed approval snapshot')
                pending = [{'run_id': run_id, 'request_id': item['request_id']} for item in fresh]
                if (not pending and state is not None and current is not None
                        and self._controls.get(run_id) is state
                        and self._run_statuses.get(run_id) is current
                        and current.get('status') == 'waiting_for_approval'
                        and current.get('run_id') == run_id
                        and not state['closed'] and run_id not in self._stopping_run_ids
                        and state.get('queue') is not None
                        and self._run_streams.get(run_id) is state['queue']
                        and session == run_id == state.get('approval_session')
                        and agent is not None and state.get('agent') is agent
                        and self._active_run_agents.get(run_id) is agent
                        and task is not None and self._active_run_tasks.get(run_id) is task
                        and task.done() is False):
                    self._set_run_status(run_id, 'running')
            except Exception:
                pending = None  # Missing/changed evidence never authorizes recovery.
            try:
                yield pending
            finally:
                if locked:
                    lock.release()

        async def _handle_get_run(self, request):
            response = await super()._handle_get_run(request)
            if response.status != 200:
                return response
            run_id = request.match_info['run_id']
            with self._controls_lock, self._approval_snapshot(run_id) as pending:
                data = dict(self._run_statuses.get(run_id, json.loads(response.text)))
                if pending is not None:
                    data['pending_approvals'] = pending
                data.update(self._snapshot(run_id))
                return web.json_response(data)

        async def _handle_capabilities(self, request):
            response = await super()._handle_capabilities(request)
            if response.status != 200:
                return response
            data = json.loads(response.text)
            data['mobile_run_controls'] = {
                'version': 1, 'steering': True, 'live_commentary': True,
                'clarifications': True}
            data['mobile_run_controls_v1'] = True
            data['mobile_photos'] = {
                'version': 1, 'max_images': 4, 'max_image_bytes': 2 * 1024 * 1024,
                'max_request_bytes': PHOTO_REQUEST_BYTES, 'private_persistence': True}
            return web.json_response(data)
    return RunControlsAdapter

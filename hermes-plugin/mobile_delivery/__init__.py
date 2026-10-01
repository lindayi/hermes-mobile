"""Outbound-only cron delivery platform. No agent/session injection hooks."""
from pathlib import Path
import re

import httpx
from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, SendResult
from hermes_constants import get_hermes_home

ENDPOINT = 'http://127.0.0.1:9120/hermes/app-api/internal/deliver'
ORIGIN = 'https://lindayi.me'


class MobileDeliveryAdapter(BasePlatformAdapter):
    # The bridge accepts an entire result atomically; opt out of the router's
    # 4k fallback truncation. Never split a run into multiple dedup-identical sends.
    splits_long_messages = True

    async def connect(self, *, is_reconnect=False):
        self._mark_connected()
        return True

    async def disconnect(self):
        self._mark_disconnected()

    async def get_chat_info(self, chat_id):
        return {'name': chat_id, 'type': 'dm'}

    def __init__(self, config):
        super().__init__(config=config, platform=Platform('mobile_delivery'))
        extra = config.extra or {}
        self.profile = extra.get('profile')
        self.bindings = extra.get('bindings')
        if (not isinstance(self.profile, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', self.profile)
                or not isinstance(self.bindings, dict) or not self.bindings
                or any(not isinstance(recipient, str) or not recipient
                       or (jobs != 'profile' and (not isinstance(jobs, list) or not jobs
                           or any(not isinstance(job, str) or not re.fullmatch(r'[a-f0-9]{12}', job) for job in jobs)))
                       for recipient, jobs in self.bindings.items())):
            raise ValueError('Explicit profile and recipient/job bindings are required')
        self.home = home = get_hermes_home().resolve()
        name = extra.get('token_file', '')
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise ValueError('token_file must be relative to the active Hermes home')
        path = (home / name).resolve()
        if not path.is_relative_to(home) or not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError('token_file must be a private file inside the active Hermes home')
        self.token = path.read_text().strip()
        if not self.token or any(ord(c) < 33 or ord(c) > 126 for c in self.token):
            raise ValueError('Invalid delivery token')

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        metadata = metadata or {}
        binding = self.bindings.get(chat_id, [])
        job_id = metadata.get('job_id')
        if binding == 'profile':
            from cron.jobs import get_job, use_cron_store
            # Bind to the home captured at construction, not ambient mutable
            # profile state on a shared gateway event loop. Read-only lookup.
            with use_cron_store(self.home):
                bound = isinstance(job_id, str) and get_job(job_id) is not None
        else:
            bound = job_id in binding
        if (not bound
                or not isinstance(metadata.get('run_id'), str)
                or not metadata['run_id'].strip()):
            return SendResult(success=False, error='Missing stable execution identity or recipient binding')
        from cron.scheduler import _is_cron_silence_response
        if not content.strip() or _is_cron_silence_response(content):
            return SendResult(success=True, raw_response={'delivered': False, 'silent': True})
        payload = {
            'profile': self.profile, 'job_id': metadata['job_id'],
            'run_id': metadata['run_id'], 'title': 'Scheduled update',
            'body': content, 'historical': False,
        }
        import asyncio
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            for attempt in range(3):
                try:
                    response = await client.post(ENDPOINT, json=payload, headers={
                        'Authorization': 'Bearer ' + self.token, 'Origin': ORIGIN,
                    })
                    if response.status_code == 200:
                        try:
                            data = response.json()
                        except ValueError:
                            data = None
                        if (isinstance(data, dict) and data.get('status') == 'stored'
                                and isinstance(data.get('id'), str) and data['id']):
                            return SendResult(success=True, message_id=data['id'])
                        return SendResult(success=False, error='Mobile bridge receipt unconfirmed')
                    error = f'Mobile bridge HTTP {response.status_code}'
                    if response.status_code != 429 and response.status_code < 500:
                        return SendResult(success=False, error=error)
                except httpx.TransportError:
                    error = 'Mobile bridge transport failed; receipt unknown'
                if attempt < 2:
                    await asyncio.sleep(0.25 * (attempt + 1))
        return SendResult(success=False, error=error + '; bounded retries exhausted')


async def standalone_send(config, chat_id, content, *, metadata=None, **kwargs):
    if kwargs.get('media_files'):
        return {'error': 'Mobile inbox supports text only; attachments were not delivered'}
    obj = MobileDeliveryAdapter(config)
    result = await obj.send(chat_id, content, metadata=metadata)
    return {"success": True, "message_id": result.message_id} if result.success else {"error": result.error}


def register(ctx):
    ctx.register_platform(
        name='mobile_delivery', label='Mobile inbox',
        adapter_factory=MobileDeliveryAdapter, check_fn=lambda: True,
        standalone_sender_fn=standalone_send,
        cron_deliver_env_var='HERMES_MOBILE_HOME_CHANNEL',
    )

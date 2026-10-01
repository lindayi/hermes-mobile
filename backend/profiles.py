"""Clean member provisioning: logical profile separation, NOT a filesystem sandbox.

Only an explicitly injected trusted subprocess runner can create profiles. No
credentials, gateway activation, owner state, or shell aliases are copied/changed.
A durable claim prevents concurrent creation; interrupted claims require operator
reconciliation rather than automatically rerunning an uncertain subprocess.
"""
from pathlib import Path
import re
import subprocess

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

PROFILE_ROOT = Path('/home/lindayi/.hermes/profiles')


class ProvisionBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ProfileProvisioner:
    def __init__(self, auth_service, runner=None):
        self.auth = auth_service
        self.runner = runner

    @staticmethod
    def _member(db, member_id):
        member = db.execute('SELECT * FROM users WHERE id=?', (member_id,)).fetchone()
        if not member:
            raise HTTPException(404, 'Member not found')
        if (member['role'] != 'member' or member['profile'] != 'member_' + member_id
                or not re.fullmatch(r'member_[a-z0-9_-]{1,57}', member['profile'])):
            raise HTTPException(409, 'Member with canonical generated profile required')
        return member

    @staticmethod
    def _complete(target):
        return (PROFILE_ROOT.resolve() == PROFILE_ROOT and not target.is_symlink() and target.is_dir()
                and all((target / name).is_file() and not (target / name).is_symlink()
                        for name in ('SOUL.md', '.env')))

    def provision(self, request, user, member_id):
        self.auth.require_mutation(request, user)
        self.auth.require_owner(user)
        self.auth.require_fresh(user)
        key = 'provisioning:' + member_id
        with self.auth.authorized_transaction(user) as db:
            member = self._member(db, member_id)
            if member['status'] != 'pending':
                raise HTTPException(409, 'Pending member required')
            profile = member['profile']
            target = PROFILE_ROOT / profile
            if PROFILE_ROOT.resolve() != PROFILE_ROOT or target.is_symlink():
                raise HTTPException(409, 'Unsafe profile path')
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            state = row['value'] if row else 'pending'
            if state == 'creating':
                raise HTTPException(409, 'Creation in progress or interrupted; reconcile before retrying')
            already = state == 'provisioned'
            if already:
                if not self._complete(target):
                    raise HTTPException(409, 'Provisioned profile incomplete; manual reconciliation required')
            else:
                if target.exists():
                    raise HTTPException(409, 'Existing or partial profile requires manual reconciliation')
                if self.runner is None:
                    raise HTTPException(503, 'Profile provisioning is not configured')
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, 'creating'))
        if already:
            return self.status(member_id)
        try:
            result = self.runner(
                ['hermes', 'profile', 'create', profile, '--no-alias', '--no-skills'],
                shell=False, timeout=60, check=False, capture_output=True,
                env={'HOME': '/home/lindayi', 'HERMES_HOME': '/home/lindayi/.hermes',
                     'PATH': '/usr/local/bin:/usr/bin:/bin'})
            failed = result.returncode != 0 or not self._complete(target)
        except (OSError, subprocess.SubprocessError):
            failed = True
        # Record reality even if the initiating device/member was revoked while
        # the subprocess ran. Never reactivate a disabled member or its sessions.
        with self.auth.store.transaction() as db:
            db.execute('UPDATE settings SET value=? WHERE key=?',
                       ('failed' if failed else 'provisioned', key))
        if failed:
            raise HTTPException(503, 'Profile provisioning failed; inspect status before retrying')
        return self.status(member_id)

    def status(self, member_id):
        """Trusted internal lookup; public callers must use the owner-only router.

        Creating files never authorizes execution. The console-only
        backend.member_runtime verifier is responsible for setting auth ready;
        this endpoint reports that persisted status, not live listener health.
        """
        with self.auth.store.transaction() as db:
            member = self._member(db, member_id)
            row = db.execute('SELECT value FROM settings WHERE key=?', ('provisioning:' + member_id,)).fetchone()
        state = row['value'] if row else 'pending'
        if state == 'provisioned' and not self._complete(PROFILE_ROOT / member['profile']):
            state = 'failed'
        return {'id': member_id, 'profile': member['profile'], 'status': member['status'],
                'provisioning_status': state, 'ready': member['status'] == 'ready' and state == 'provisioned'}


def build_profiles_router(service):
    router = APIRouter()

    @router.post('/members/{member_id}/provision')
    def provision(member_id: str, body: ProvisionBody, request: Request,
                  user=Depends(service.auth.require_user)):
        return service.provision(request, user, member_id)

    @router.get('/members/{member_id}/provision')
    def status(member_id: str, user=Depends(service.auth.require_user)):
        service.auth.require_owner(user)
        return service.status(member_id)

    return router

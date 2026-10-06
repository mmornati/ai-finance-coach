"""First-run setup (E13-2): where the person is in the wizard, and the health of the installation. READ-ONLY.

The wizard's steps that send something outside (the Enable Banking check, a bank connection, a sync, a model run) need a typed consent in a
terminal, so the page only SHOWS the state and the command to run (``coach setup``); it has no endpoint that starts one. Nothing here
returns a secret, a name, a merchant or an amount: step names, statuses, counts, file paths of this machine.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from coach.api.deps import get_state
from coach.api.state import AppState
from coach.setup import doctor, wizard

router = APIRouter(tags=["setup"])


@router.get("/setup/wizard", summary="The seven steps of the first-run wizard and where you are (read-only; the steps run in a terminal: `coach setup`)")
def wizard_status(state: AppState = Depends(get_state)):
    return wizard.status(state.cfg)


@router.get("/setup/doctor", summary="`coach doctor` as data: the installation checks and the next steps (read-only)")
def doctor_status(state: AppState = Depends(get_state)):
    checks = doctor.run_checks(state.cfg)
    return {"checks": [c.to_dict() for c in checks], "next_steps": doctor.next_steps(checks), "ok": not any(c.level == "fail" for c in checks)}

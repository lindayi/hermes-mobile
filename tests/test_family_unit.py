import configparser
from pathlib import Path


def test_family_scheduler_unit_is_explicit_member_only_and_not_auto_retrying():
    path=Path(__file__).parents[1]/'deploy'/'hermes-family-scheduler@.service'
    assert path.exists(), 'Deployable family scheduler unit missing'
    unit=configparser.ConfigParser(interpolation=None);unit.read(path)
    command=unit['Service']['ExecStart']
    assert '-m backend.member_scheduler' in command
    assert '--member-id %i --profile member_%i' in command
    assert '--config /home/lindayi/.local/share/hermes-mobile-live/config.json' in command
    assert 'gateway' not in command
    assert unit['Service']['Restart']=='no'
    assert unit['Service']['UMask']=='0077'
    assert 'member_%i/.mobile-jobs/verified.json' in unit['Unit']['ConditionPathExists']
    assert 'EnvironmentFile' not in unit['Service']

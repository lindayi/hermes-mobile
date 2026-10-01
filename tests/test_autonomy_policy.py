from deploy.autonomy_policy import validate_transition


def test_missing_evidence_blocks_pre_cutover_readiness():
    report = validate_transition({}, phase='pre-cutover')

    assert report['ready'] is False
    assert report['phase'] == 'pre-cutover'
    assert report['blockers']

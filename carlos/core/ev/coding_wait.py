import math
import re


def waiting_summary(proposal):
    if not isinstance(proposal, dict):
        return None
    identifier = proposal.get('proposal_id')
    if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
        return None
    if proposal.get('approval_required') is not True:
        return None
    status = proposal.get('status')
    if (status == 'READY_FOR_REVIEW' and proposal.get('executed') is False
            and proposal.get('checkpoint_possible') is True
            and isinstance(proposal.get('agent'), dict) and proposal['agent'].get('available') is True):
        phase, observed = 'REVIEW_PROPOSAL', proposal.get('created_epoch')
    elif (status == 'VALIDATED_AWAITING_DEPLOYMENT_REVIEW' and proposal.get('executed') is True
          and proposal.get('deployment_approved') is False):
        commit, tests = proposal.get('commit_id'), proposal.get('tests')
        if (not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', commit)
                or not isinstance(tests, list) or not tests
                or any(not isinstance(test, dict) or test.get('passed') is not True for test in tests)):
            return None
        phase, observed = 'REVIEW_DEPLOYMENT', proposal.get('completed_epoch')
    else:
        return None
    try:
        if type(observed) not in (int, float) or not math.isfinite(observed) or observed <= 0:
            return None
    except OverflowError:
        return None
    return {'proposal_id': identifier, 'phase': phase, 'observed_at': observed, 'approval_required': True}

# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from pydantic import ValidationError
from src.modules.alerts.schemas import (
    AlertRuleCondition,
    ComplianceCriteria,
    AlertRuleThreshold,
    CreateAlertRuleRequest,
    UpdateAlertRuleRequest,
)


def test_create_request_minimal():
    req = CreateAlertRuleRequest(
        name='Test',
        type='security',
        severity='high',
        target_filters=[AlertRuleCondition(field='category', op='=', value='Server')],
    )
    assert req.enabled is True
    assert req.description == ''
    assert req.threshold is None
    assert req.silence_duration is None
    assert req.compliance_criteria is None


def test_condition_accepts_array_value_for_list_ops():
    # any/all/none/between send a string[] end-to-end (D-1); the alert schema must
    # accept it rather than 422 (value was `str`-only before).
    cond = AlertRuleCondition(field='data_source', op='any', value=['jamf', 'sccm'])
    assert cond.value == ['jamf', 'sccm']


def test_condition_accepts_scalar_string_value():
    cond = AlertRuleCondition(field='category', op='eq', value='Server')
    assert cond.value == 'Server'


def test_create_request_full():
    req = CreateAlertRuleRequest(
        name='Server EDR Coverage',
        description='EDR below 90%',
        type='compliance',
        severity='high',
        target_filters=[AlertRuleCondition(field='category', op='=', value='Server')],
        compliance_criteria=ComplianceCriteria(
            logic='all',
            conditions=[AlertRuleCondition(field='has_edr', op='=', value='true')],
        ),
        threshold=AlertRuleThreshold(condition='falls_below', value=90, unit='percent'),
        silence_duration=480,
    )
    assert req.threshold.condition == 'falls_below'


def test_create_rejects_changes_by():
    with pytest.raises(ValidationError):
        AlertRuleThreshold(condition='changes_by', value=10, unit='percent')


def test_create_rejects_empty_target_filters():
    with pytest.raises(ValidationError):
        CreateAlertRuleRequest(
            name='Test', type='security', severity='high', target_filters=[]
        )


def test_create_rejects_silence_over_week():
    with pytest.raises(ValidationError):
        CreateAlertRuleRequest(
            name='Test', type='security', severity='high',
            target_filters=[AlertRuleCondition(field='x', op='=', value='y')],
            silence_duration=10081,
        )


def test_create_rejects_invalid_type():
    with pytest.raises(ValidationError):
        CreateAlertRuleRequest(
            name='Test', type='invalid', severity='high',
            target_filters=[AlertRuleCondition(field='x', op='=', value='y')],
        )


def test_create_rejects_invalid_severity():
    with pytest.raises(ValidationError):
        CreateAlertRuleRequest(
            name='Test', type='security', severity='nope',
            target_filters=[AlertRuleCondition(field='x', op='=', value='y')],
        )


def test_update_request_all_optional():
    req = UpdateAlertRuleRequest()
    assert req.name is None
    assert req.target_filters is None

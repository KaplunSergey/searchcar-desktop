"""Artifact contracts, not proof that iPhone imports/runs the generated shortcut."""
import importlib.util
from pathlib import Path
import plistlib


def test_reminder_shortcut_uses_only_data_parsing_confirmation_and_one_explicit_write():
    source = Path(__file__).resolve().parents[2]/'scripts/build_reminder_shortcut.py'
    spec = importlib.util.spec_from_file_location('reminder_builder', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = module.reminder_shortcut()
    assert plistlib.loads(plistlib.dumps(value)) == value
    assert value['WFWorkflowName'] == 'SearchCar Reminder'
    actions = value['WFWorkflowActions']
    identifiers = [action['WFWorkflowActionIdentifier'] for action in actions]
    assert set(identifiers) <= {'is.workflow.actions.'+name for name in (
        'comment', 'detect.dictionary', 'getvalueforkey', 'date', 'alert', 'addnewreminder', 'conditional', 'exit')}
    assert identifiers.count('is.workflow.actions.addnewreminder') == 1
    guards = [action['WFWorkflowActionParameters'] for action in actions
              if action['WFWorkflowActionIdentifier'] == 'is.workflow.actions.conditional'
              and action['WFWorkflowActionParameters']['WFControlFlowMode'] == 0]
    assert any(guard['WFCondition'] == 1 and guard['WFDate']['Value']['Type'] == 'CurrentDate' for guard in guards), \
        'the command itself must reject a past alert, even when an old launch URL is reused'
    assert any(guard['WFCondition'] == 101 for guard in guards), 'empty required values must stop before the write'
    assert actions[-2]['WFWorkflowActionParameters']['WFAlertActionCancelButtonShown'] is True
    parameters = actions[-1]['WFWorkflowActionParameters']
    assert parameters['WFCalendarItemAlert'] is True and parameters['WFAlertTrigger'] == 'At Time'
    assert value['WFWorkflowImportQuestions'][0]['ActionIndex'] == len(actions)-1
    assert value['WFWorkflowImportQuestions'][0]['ParameterKey'] == 'WFCalendarItemCalendar'
    defined = set()
    for action in actions:
        parameters = action['WFWorkflowActionParameters']
        def check(item):
            if isinstance(item, dict):
                if item.get('Type') == 'ActionOutput':
                    assert item['OutputUUID'] in defined
                for nested in item.values(): check(nested)
            elif isinstance(item, list):
                for nested in item: check(nested)
        check(parameters)
        assert parameters['UUID'] not in defined
        defined.add(parameters['UUID'])

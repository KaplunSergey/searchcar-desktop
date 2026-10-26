"""Generate the data-only Apple Shortcuts prototype; never import or run it."""
from pathlib import Path
import argparse
import plistlib
from uuid import NAMESPACE_URL, uuid5


def reminder_shortcut():
    actions = []

    def action(name, **parameters):
        identity = str(uuid5(NAMESPACE_URL, f'searchcar-reminder-v1:{len(actions)}')).upper()
        actions.append({'WFWorkflowActionIdentifier': 'is.workflow.actions.'+name,
                        'WFWorkflowActionParameters': {'UUID': identity, **parameters}})
        return {'WFSerializationType': 'WFTextTokenAttachment',
                'Value': {'Type': 'ActionOutput', 'OutputUUID': identity}}

    def reject_if(reference, condition, message, **comparison):
        group = str(uuid5(NAMESPACE_URL, f'searchcar-reminder-guard:{len(actions)}')).upper()
        action('conditional', GroupingIdentifier=group, WFControlFlowMode=0,
               WFInput={'Type': 'Variable', 'Variable': reference}, WFCondition=condition, **comparison)
        action('alert', WFAlertActionTitle='SearchCar', WFAlertActionMessage=message,
               WFAlertActionCancelButtonShown=False)
        action('exit')
        action('conditional', GroupingIdentifier=group, WFControlFlowMode=2)

    action('comment', WFCommentActionText='SearchCar Reminder — prototype. Select an iCloud list during import. '
        'Accepts public event JSON only; no network, files, credentials or automatic background execution. '
        'Verify the date and link in the confirmation. Imported reminders do not follow auction reschedules.')
    data = action('detect.dictionary', WFInput={'WFSerializationType': 'WFTextTokenAttachment',
                                              'Value': {'Type': 'ExtensionInput'}})
    title = action('getvalueforkey', WFInput=data, WFGetDictionaryValueType='Value', WFDictionaryKey='title')
    reject_if(title, 101, 'Нет названия / Немає назви. Откройте актуальную ссылку из SearchCar.')
    alert = action('getvalueforkey', WFInput=data, WFGetDictionaryValueType='Value', WFDictionaryKey='alert_utc')
    reject_if(alert, 101, 'Нет времени / Немає часу. Откройте актуальную ссылку из SearchCar.')
    date = action('date', WFDateActionMode='Specified Date', WFDateActionDate=alert)
    reject_if(date, 101, 'Неверная дата / Невірна дата. Откройте актуальную ссылку из SearchCar.')
    reject_if(date, 1, 'Время напоминания уже прошло / Час нагадування вже минув.',
              WFDate={'WFSerializationType': 'WFTextTokenAttachment', 'Value': {'Type': 'CurrentDate'}})
    notes = action('getvalueforkey', WFInput=data, WFGetDictionaryValueType='Value', WFDictionaryKey='notes')
    text = 'Добавить напоминание / Додати нагадування?\n\ufffc\nУведомить / Сповістити:\n\ufffc\n\ufffc'
    positions = [i for i, character in enumerate(text) if character == '\ufffc']
    confirmation = {'WFSerializationType': 'WFTextTokenString', 'Value': {
        'string': text, 'attachmentsByRange': {f'{{{index}, 1}}': reference['Value']
                                              for index, reference in zip(positions, (title, date, notes))}}}
    action('alert', WFAlertActionTitle='SearchCar', WFAlertActionMessage=confirmation,
           WFAlertActionCancelButtonShown=True)
    add_index = len(actions)
    action('addnewreminder', WFCalendarItemTitle=title, WFCalendarItemNotes=notes,
           WFCalendarItemCalendar='', WFCalendarItemAlert=True, WFAlertTrigger='At Time', WFAlertCustomTime=date)
    return {'WFWorkflowName': 'SearchCar Reminder', 'WFWorkflowActions': actions,
            'WFWorkflowClientVersion': '2600', 'WFWorkflowMinimumClientVersion': 900,
            'WFWorkflowMinimumClientVersionString': '900',
            'WFWorkflowIcon': {'WFWorkflowIconStartColor': 4282601983, 'WFWorkflowIconGlyphNumber': 59511},
            'WFWorkflowInputContentItemClasses': ['WFStringContentItem'],
            'WFWorkflowOutputContentItemClasses': [], 'WFWorkflowTypes': [], 'WFQuickActionSurfaces': [],
            'WFWorkflowHasShortcutInputVariables': True,
            'WFWorkflowImportQuestions': [{'Category': 'Parameter', 'ActionIndex': add_index,
                'ParameterKey': 'WFCalendarItemCalendar',
                'Text': 'Выберите список iCloud / Виберіть список iCloud. Так напоминания будут доступны и на Mac.',
                'DefaultValue': ''}]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        plistlib.dump(reminder_shortcut(), stream, sort_keys=False)
    print(output)

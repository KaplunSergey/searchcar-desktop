import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Locale } from './translations';
import type { SourceWorkspaceProps } from './source-workspace-contract';

export type SearchSelection = {
  id: string; source_key: string; status: 'SELECTING' | 'READY' | 'CLOSED' | 'FAILED';
  issue: string | null; can_use: boolean;
  project_id?: number;
  snapshot: { summary: string[]; revision: string; creation_url: string | null } | null;
};

export function selectionIssueMessage(issue: string, locale: Locale) {
  const messages: Record<string, [string, string]> = {
    NO_SEARCH_CAPTURED: ['Условия ещё не получены. Примените фильтр на сайте, чтобы обновить результаты.', 'Умови ще не отримано. Застосуйте фільтр на сайті, щоб оновити результати.'],
    SEARCH_RESULTS_NOT_READY: ['Фильтры получены. Ожидаем загрузки списка автомобилей.', 'Фільтри отримано. Очікуємо завантаження списку автомобілів.'],
    SITE_VERIFICATION_REQUIRED: ['Пройдите проверку сайта вручную. Затем дождитесь результатов.', 'Пройдіть перевірку сайту вручну. Потім дочекайтеся результатів.'],
    SITE_ACCESS_BLOCKED: ['Сайт ограничил доступ. Пока результаты недоступны, сохранить выбор нельзя.', 'Сайт обмежив доступ. Поки результати недоступні, зберегти вибір не можна.'],
    SITE_CONSENT_REQUIRED: ['Выберите настройки cookies в окне сайта.', 'Виберіть налаштування cookies у вікні сайту.'],
    SEARCH_REQUEST_FAILED: ['Сайт не загрузил результаты. Повторно примените фильтры на сайте.', 'Сайт не завантажив результати. Повторно застосуйте фільтри на сайті.'],
    SOURCE_SELECTION_RESET_UNAVAILABLE: ['IAA не подтвердил сброс фильтров. Откройте выбор повторно; прежний поиск не будет использован для нового проекта.', 'IAA не підтвердив скидання фільтрів. Відкрийте вибір повторно; попередній пошук не буде використано для нового проєкту.'],
    LAST_CHANGE_UNCONFIRMED: ['Последнее изменение не подтверждено. Прежние условия сохранены, но перед созданием проекта нужно повторно выбрать и подтвердить фильтры.', 'Останню зміну не підтверджено. Попередні умови збережено, але перед створенням проєкту потрібно повторно вибрати та підтвердити фільтри.'],
    SEARCH_FILTERS_UNSUPPORTED: ['Формат фильтров не распознан. Создание проекта заблокировано, чтобы не потерять условия.', 'Формат фільтрів не розпізнано. Створення проєкту заблоковано, щоб не втратити умови.'],
  };
  return (messages[issue === 'SELECTION_INTERRUPTED' ? 'LAST_CHANGE_UNCONFIRMED' : issue]
    || ['Не удалось подтвердить успешный поиск на сайте. Откройте выбор повторно.', 'Не вдалося підтвердити успішний пошук на сайті. Відкрийте вибір повторно.'])[locale === 'uk' ? 1 : 0];
}

export function SelectionSummary({ selection, locale }: { selection: SearchSelection | null; locale: Locale }) {
  const l = (ru: string, uk: string) => locale === 'uk' ? uk : ru;
  if (!selection) return <p>{l('Выберите фильтры на сайте. Закрытие окна сохранит последние успешно применённые условия.', 'Виберіть фільтри на сайті. Закриття вікна збереже останні успішно застосовані умови.')}</p>;
  const snapshot = selection.snapshot;
  return <>
    <p role="status">{selection.status === 'FAILED'
      ? selection.issue === 'SOURCE_SELECTION_RESET_UNAVAILABLE'
        ? l('Не удалось подготовить новый поиск IAA.', 'Не вдалося підготувати новий пошук IAA.')
        : l('Окно выбора недоступно. Дождитесь окончания поиска или закройте другое окно этого сайта в SearchCar и повторите.', 'Вікно вибору недоступне. Дочекайтеся завершення пошуку або закрийте інше вікно цього сайту в SearchCar та повторіть.')
      : selection.status === 'CLOSED'
        ? selection.can_use ? l('Условия сохранены в черновике.', 'Умови збережено в чернетці.') : l('Выбор не завершён.', 'Вибір не завершено.')
        : selection.status === 'READY'
          ? l('Условия применены. Подтвердите выбор или закройте окно сайта.', 'Умови застосовано. Підтвердьте вибір або закрийте вікно сайту.')
          : l('Выберите фильтры на сайте. Панель SearchCar внизу окна покажет, когда выбор можно сохранить.', 'Виберіть фільтри на сайті. Панель SearchCar унизу вікна покаже, коли вибір можна зберегти.')}</p>
    {snapshot && <div className="search-selection-summary"><strong>{l('Сохранённые условия', 'Збережені умови')}</strong>
      {snapshot.summary.length ? <ul>{snapshot.summary.map((value, index) => <li key={index}>{value}</li>)}</ul>
        : <p>{l('Все автомобили — без дополнительных фильтров.', 'Усі автомобілі — без додаткових фільтрів.')}</p>}</div>}
    {selection.issue && <p className="search-selection-warning" role="status">{selectionIssueMessage(selection.issue, locale)}</p>}
    {selection.can_use && snapshot && !snapshot.creation_url && <p className="search-selection-warning" role="status">{l(
      'Условия захвачены, но нет ссылки для их восстановления. Откройте выбор повторно, примените фильтр и дождитесь результатов. Общая ссылка не будет использована вместо фильтров.',
      'Умови захоплено, але немає посилання для їх відновлення. Відкрийте вибір повторно, застосуйте фільтр і дочекайтеся результатів. Загальне посилання не буде використано замість фільтрів.')}</p>}
  </>;
}

export function SearchSelector({ source, draftId, projectId, locale, read, write, canSearch, disabled=false, help, onChange, onOpeningChange }: {
  source: string; locale: Locale; read: SourceWorkspaceProps['read']; write: NonNullable<SourceWorkspaceProps['write']>;
  draftId: string; projectId?: number; canSearch: boolean; disabled?: boolean; help?: string; onChange: (selection: SearchSelection | null) => void;
  onOpeningChange?: (opening: boolean) => void;
}) {
  const l = (ru: string, uk: string) => locale === 'uk' ? uk : ru;
  const client = useQueryClient(), key = ['search-selection', source, draftId];
  const path = `/source-search/${source}/selection`;
  const query = useQuery<{ selection: SearchSelection | null }>({ queryKey: key, queryFn: () => read(`${path}?draft_id=${draftId}`), retry: false,
    refetchInterval: (query) => ['SELECTING', 'READY'].includes(query.state.data?.selection?.status || '') ? 1000 : false });
  const selection = query.data?.selection || null;
  const mutation = useMutation({ mutationFn: (action: 'OPEN' | 'CONFIRM') => write<{ selection: SearchSelection }>(path, { action, selection_id: selection?.id, draft_id: draftId, ...(projectId ? { project_id: projectId } : {}) }),
    onMutate: () => { onChange(null); },
    onSuccess: (value) => { client.setQueryData(key, value); onChange(value.selection); void query.refetch(); },
    onSettled: () => { onOpeningChange?.(false); } });
  useEffect(() => { onChange(query.isError || mutation.isPending ? null : selection); }, [selection, query.isError, mutation.isPending, onChange]);
  const active = selection?.status === 'SELECTING' || selection?.status === 'READY';
  return <section className="search-selection" aria-label={l('Условия поиска', 'Умови пошуку')}>
    <div className="search-selection-actions">
      <button type="button" className="button" disabled={!canSearch || disabled || active || mutation.isPending || query.isPending} onClick={() => { onOpeningChange?.(true); mutation.mutate('OPEN'); }}>
        {active ? l('Выбор открыт на сайте', 'Вибір відкрито на сайті') : selection?.snapshot ? l('Изменить фильтры', 'Змінити фільтри') : l('Выбрать авто для поиска', 'Вибрати авто для пошуку')}</button>
      {active && <button type="button" className="button primary" disabled={disabled || mutation.isPending || query.isError || selection?.status !== 'READY' || !selection.can_use || !canSearch} onClick={() => { onOpeningChange?.(true); mutation.mutate('CONFIRM'); }}>
        {l('Использовать выбранные фильтры', 'Використати вибрані фільтри')}</button>}
    </div>
    {help && <p>{help}</p>}
    {query.isPending ? <p role="status">{l('Чтение черновика…', 'Читання чернетки…')}</p> : <SelectionSummary selection={selection} locale={locale} />}
    {(query.isError || mutation.isError) && <p className="search-selection-warning" role="alert">{l('Не удалось открыть или прочитать выбор. Проверьте доступ к сайту и лицензии.', 'Не вдалося відкрити або прочитати вибір. Перевірте доступ до сайту та ліцензії.')} <button type="button" className="button" onClick={() => { mutation.reset(); void query.refetch(); }}>{l('Повторить', 'Повторити')}</button></p>}
    {!canSearch && <p>{l('Для открытия сайта необходим доступ по лицензии.', 'Для відкриття сайту потрібен доступ за ліцензією.')}</p>}
  </section>;
}

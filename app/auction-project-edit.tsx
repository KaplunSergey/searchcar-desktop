import { useCallback, useEffect, useId, useRef, useState, type RefObject } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { SearchSelector, type SearchSelection } from './search-selection';
import type { SourceWorkspaceProps } from './source-workspace-contract';
import type { Locale } from './translations';

type SavedSearch = { id: number; source_key: string; name: string; search_revision: number; search_config: { summary: string[] } | null };

export function AuctionProjectEdit({ projectId, source, locale, canSearch, busy, read, write, saved, close, returnFocus, fallbackFocus }: {
  projectId: number; source: string; locale: Locale; canSearch: boolean; busy: boolean;
  read: SourceWorkspaceProps['read']; write: NonNullable<SourceWorkspaceProps['write']>;
  saved: () => void; close: () => void;
  returnFocus: RefObject<HTMLButtonElement | null>; fallbackFocus: RefObject<HTMLHeadingElement | null>;
}) {
  const l = (ru: string, uk: string) => locale === 'uk' ? uk : ru;
  const dialog = useRef<HTMLDialogElement>(null), submitting = useRef(false);
  const [draftId] = useState(() => crypto.randomUUID().replaceAll('-', ''));
  const [selection, setSelection] = useState<SearchSelection | null>(null);
  const [opening, setOpening] = useState(false);
  const openingRef = useRef(false);
  const openingChanged = useCallback((value: boolean) => { openingRef.current = value; setOpening(value); }, []);
  const titleId = useId(), helpId = useId(), client = useQueryClient();
  const query = useQuery<SavedSearch>({ queryKey: ['project-search-edit', source, projectId, draftId],
    queryFn: () => read(`/projects/${projectId}`), retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false });
  const project = query.data?.id === projectId && query.data.source_key === source ? query.data : null;
  const usable = !!project && Number.isSafeInteger(project.search_revision) && selection?.source_key === source
    && selection.project_id === projectId && selection.status === 'CLOSED' && selection.can_use && !!selection.snapshot?.creation_url;
  const mutation = useMutation({ mutationFn: () => {
    if (!usable || !canSearch || busy) throw new Error('Unconfirmed selection');
    return write(`/projects/${projectId}/search-selection`, { selection_id: selection!.id, draft_id: draftId, expected_revision: project!.search_revision });
  }, onSuccess: () => {
    for (const key of [['projects'], ['project', projectId], ['launch-options'], ['scheduler']]) void client.invalidateQueries({ queryKey: key });
    saved();
  }, onSettled: () => { submitting.current = false; } });
  const cancel = useMutation({ mutationFn: async () => {
    if (selection && ['SELECTING', 'READY'].includes(selection.status))
      await write(`/source-search/${source}/selection`, { action: 'CANCEL', project_id: projectId, draft_id: draftId, selection_id: selection.id });
  }, onSuccess: close });
  const pending = mutation.isPending || cancel.isPending || opening;
  const dismiss = () => { if (!submitting.current && !openingRef.current && !pending) cancel.mutate(); };
  useEffect(() => {
    const node = dialog.current;
    const origin = returnFocus.current, fallback = fallbackFocus.current;
    node?.showModal();
    return () => { node?.close(); if (origin?.isConnected) origin.focus(); else fallback?.focus(); };
  }, [returnFocus, fallbackFocus]);
  const code = (mutation.error as { code?: string } | null)?.code;
  const error = code === 'project_search_active' ? l('Проект уже обновляется. Дождитесь завершения или отмените обновление.', 'Проєкт уже оновлюється. Дочекайтеся завершення або скасуйте оновлення.')
    : code === 'project_search_changed' ? l('Фильтры проекта уже изменились в другом окне. Закройте форму и откройте её заново.', 'Фільтри проєкту вже змінилися в іншому вікні. Закрийте форму й відкрийте її знову.')
    : code === 'project_name_or_url_exists' ? l('Проект с такими условиями уже существует на этом сайте.', 'Проєкт із такими умовами вже існує на цьому сайті.')
    : code === 'search_selection_changed' ? l('Выбор изменился или не подтверждён. Повторно выберите фильтры.', 'Вибір змінився або не підтверджений. Повторно виберіть фільтри.')
    : l('Сохранение не подтверждено. Проверьте доступ и повторите; если условия уже изменились, откройте форму заново.', 'Збереження не підтверджено. Перевірте доступ і повторіть; якщо умови вже змінилися, відкрийте форму знову.');
  return <dialog ref={dialog} className="launch-dialog auction-project-edit" aria-labelledby={titleId} aria-describedby={helpId}
    aria-busy={pending} onCancel={event => { event.preventDefault(); dismiss(); }}>
    <h2 id={titleId}>{l('Изменить фильтры проекта', 'Змінити фільтри проєкту')}{project && ` · ${project.name}`}</h2>
    <p id={helpId}>{l('Выберите новые условия на сайте и подтвердите их. Отмена не меняет проект. После сохранения начнётся поиск; история и исключённые автомобили сохранятся.', 'Виберіть нові умови на сайті й підтвердьте їх. Скасування не змінює проєкт. Після збереження почнеться пошук; історія та виключені автомобілі збережуться.')}</p>
    {query.isPending ? <p role="status">{l('Чтение условий проекта…', 'Читання умов проєкту…')}</p> : !project || query.isError
      ? <p role="alert">{l('Не удалось прочитать проект.', 'Не вдалося прочитати проєкт.')} <button type="button" className="button" onClick={() => { void query.refetch(); }}>{l('Повторить', 'Повторити')}</button></p>
      : <><section className="search-selection-summary"><strong>{l('Действующие условия', 'Чинні умови')}</strong>
        {project.search_config?.summary?.length ? <ul>{project.search_config.summary.map((label, index) => <li key={index}>{label}</li>)}</ul>
          : <p>{l('Условия заданы сохранённой ссылкой. Откройте сайт для изменения.', 'Умови задано збереженим посиланням. Відкрийте сайт для зміни.')}</p>}</section>
        <SearchSelector source={source} draftId={draftId} projectId={projectId} locale={locale} read={read} write={write}
          canSearch={canSearch} disabled={busy || pending} onChange={setSelection} onOpeningChange={openingChanged} /></>}
    {busy && <p role="status">{l('Сначала дождитесь завершения обновления проекта или отмените его.', 'Спочатку дочекайтеся завершення оновлення проєкту або скасуйте його.')}</p>}
    {mutation.isError && <p className="launch-error" role="alert">{error}</p>}
    {cancel.isError && <p className="launch-error" role="alert">{l('Не удалось закрыть выбор. Закройте окно сайта вручную и повторите отмену.', 'Не вдалося закрити вибір. Закрийте вікно сайту вручну й повторіть скасування.')}</p>}
    <div className="launch-actions"><button type="button" className="button" autoFocus disabled={pending} onClick={dismiss}>{l('Отмена', 'Скасувати')}</button>
      <button type="button" className="button primary" disabled={!usable || !canSearch || busy || pending} onClick={() => {
        if (submitting.current) return; submitting.current = true; mutation.mutate();
      }}>{mutation.isPending ? l('Сохранение…', 'Збереження…') : l('Сохранить и обновить', 'Зберегти й оновити')}</button></div>
  </dialog>;
}

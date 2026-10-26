import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueries } from "@tanstack/react-query";
import { computerTimeZone, type AuctionSnapshotView } from "./auction-time-client";
import type { ActivityRun } from "./activity-dialog";
import type { SourceWorkspaceProps } from "./source-workspace-contract";

type ReportItem = NonNullable<NonNullable<ActivityRun["payload"]>["report"]>[number];
export type AuctionReportResponse = { scan_id: number; source_key: string; computer_timezone: string; items: {
  car_id: number; snapshot_id: number | null; recorded_at: string | null; issue: string | null; view: AuctionSnapshotView | null;
}[] };

export function auctionReportChanges(report: ReportItem[], filter: string) {
  return report.filter(item => filter === "all" || (filter === "schedule" ? item.changes?.some(change => change.field === "schedule")
    : filter === "new" ? item.change === "NEW" : item.change !== "NEW"));
}

export function auctionReportBatches(report: ReportItem[]) {
  const captured = report.filter(item => Number.isSafeInteger(item.after_snapshot_id) && item.after_snapshot_id! > 0);
  const batches: ReportItem[][] = [];
  for (let offset = 0; offset < captured.length; offset += 20) batches.push(captured.slice(offset, offset + 20));
  return batches;
}

export function checkedAuctionReport(response: AuctionReportResponse, source: string, scanId: number, batch: ReportItem[]) {
  if (response?.source_key !== source || response.scan_id !== scanId || !Array.isArray(response.items)
      || response.items.length !== batch.length || new Set(response.items.map(item => item.car_id)).size !== batch.length) throw new Error("Invalid report scope");
  for (const item of response.items) {
    const expected = batch.find(record => record.car_id === item.car_id);
    if (!expected || item.view && (item.view.lot?.source_key !== source || item.view.lot.id !== item.car_id
      || item.snapshot_id !== expected.after_snapshot_id || item.view.generated_at !== item.recorded_at
      || !item.recorded_at || !Number.isFinite(Date.parse(item.recorded_at)))) throw new Error("Invalid report observation");
  }
  return response;
}

function changeValue(value: unknown, uk: boolean): string {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return value ? (uk ? "Так" : "Да") : (uk ? "Ні" : "Нет");
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (typeof value !== "object" || Array.isArray(value)) return uk ? "Дані змінено" : "Данные изменены";
  const record = value as Record<string, unknown>;
  if (typeof record.value === "number") return `${record.value}${typeof record.unit === "string" ? ` ${record.unit}` : ""}`;
  const textValue = [record.raw, record.utc_at, record.local_date].find((item): item is string => typeof item === "string" && !!item);
  return textValue || (uk ? "Відомості змінено" : "Сведения изменены");
}

export function AuctionHistoryReport({ report, filter, setFilter, uk, source, scanId, read, renderSnapshot }: {
  report: ReportItem[]; filter: string; setFilter: (value: string) => void; uk: boolean; source: string; scanId: number;
  read: SourceWorkspaceProps["read"]; renderSnapshot: (view: AuctionSnapshotView, recordedAt: string) => ReactNode;
}) {
  const [page, setPage] = useState({ filter, visible: 20 });
  const visible = page.filter === filter ? page.visible : 20;
  const container = useRef<HTMLElement>(null);
  const zone = computerTimeZone();
  const filtered = auctionReportChanges(report, filter), shown = filtered.slice(0, visible);
  const batches = auctionReportBatches(shown);
  const queries = useQueries({ queries: batches.map(batch => ({
    queryKey: ["auction-report", source, scanId, zone, batch.map(item => [item.car_id, item.after_snapshot_id])],
    queryFn: async () => {
      const params = new URLSearchParams([["source_key", source], ...batch.map(item => ["car_ids", String(item.car_id)])]);
      return checkedAuctionReport(await read<AuctionReportResponse>(`/scans/${scanId}/auction-report?${params}`), source, scanId, batch);
    }, enabled: !!zone && !!read, staleTime: 60_000, refetchOnWindowFocus: false, retry: false,
  })) });
  useEffect(() => { if (visible > 20) container.current?.querySelectorAll<HTMLElement>("[data-report-item]")[visible - 20]?.focus(); }, [visible]);
  const fieldLabels: Record<string, string> = { schedule: uk ? "Розклад аукціону" : "Расписание аукциона", mileage: uk ? "Показання одометра" : "Показание одометра",
    primary_damage: uk ? "Пошкодження" : "Повреждения", engine_status: uk ? "Стан двигуна" : "Состояние двигателя" };
  return <section ref={container} className="auction-history-changes">
    <div className="auction-history-changes-head"><h3>{uk ? "Зміни" : "Изменения"}: {report.length}</h3>
      <select aria-label={uk ? "Фільтр змін" : "Фильтр изменений"} value={filter} onChange={event => {
        const value = event.target.value; setPage({ filter: value, visible: 20 }); setFilter(value);
      }}>
        <option value="all">{uk ? "Усі зміни" : "Все изменения"}</option><option value="new">{uk ? "Нові автомобілі" : "Новые автомобили"}</option>
        <option value="updated">{uk ? "Оновлені автомобілі" : "Обновлённые автомобили"}</option><option value="schedule">{uk ? "Зміни розкладу" : "Изменения расписания"}</option>
      </select></div>
    <p className="auction-history-help">{uk ? "Збережені зміни на момент запуску. Поточні дані автомобілів не підставляються в цей звіт." : "Сохранённые изменения на момент запуска. Текущие данные автомобилей не подставляются в этот отчёт."}</p>
    {!zone && !!shown.length && <p role="alert">{uk ? "Не вдалося визначити часовий пояс комп’ютера. Історичні картки недоступні." : "Не удалось определить часовой пояс компьютера. Исторические карточки недоступны."}</p>}
    {!filtered.length && <p className="auction-history-empty">{!report.length ? (uk ? "У звіті немає зафіксованих змін." : "В отчёте нет зафиксированных изменений.") : (uk ? "Немає змін за цим фільтром." : "Нет изменений по этому фильтру.")}</p>}
    {shown.map((item, index) => {
      const batchIndex = batches.findIndex(batch => batch.includes(item)), query = queries[batchIndex];
      const snapshot = query?.data?.items.find(record => record.car_id === item.car_id);
      return <article className="auction-history-change" data-report-item={item.car_id} tabIndex={-1} key={`${item.project_id}-${item.car_id}-${index}`}>
        <div><span className="badge outline">{item.change === "NEW" ? (uk ? "Новий автомобіль" : "Новый автомобиль") : (uk ? "Дані змінено" : "Данные изменены")}</span>
          <small>{item.source_listing_id ? `№ ${item.source_listing_id} · ` : ""}{uk ? "Проєкт" : "Проект"} {(item.project_ids || [item.project_id]).map(id => `#${id}`).join(", ")}</small></div>
        {snapshot?.view && snapshot.recorded_at ? renderSnapshot(snapshot.view, snapshot.recorded_at) : <><h4>{item.title || (uk ? "Автомобіль" : "Автомобиль")}</h4>
          <p className="auction-history-help" role={query?.isPending && zone ? "status" : undefined}>{query?.isPending && zone ? (uk ? "Завантаження збереженої картки…" : "Загрузка сохранённой карточки…")
            : query?.isError ? (uk ? "Не вдалося завантажити картку. Перелік змін збережено." : "Не удалось загрузить карточку. Перечень изменений сохранён.")
            : (uk ? "Історичний знімок недоступний. Актуальні дані не підставляються." : "Исторический снимок недоступен. Актуальные данные не подставляются.")}</p></>}
        {!!item.changes?.length && <dl>{item.changes.map((change, i) => <div key={i}><dt>{fieldLabels[change.field] || (uk ? "Інші відомості" : "Другие сведения")}</dt>
          <dd><span>{changeValue(change.old, uk)}</span><span aria-hidden="true"> → </span><span>{changeValue(change.new, uk)}</span></dd></div>)}</dl>}
      </article>;
    })}
    {queries.map((query, index) => query.isError && <p className="launch-error" role="alert" key={index}>{uk ? "Помилка читання збережених карток." : "Ошибка чтения сохранённых карточек."} <button className="button" onClick={() => void query.refetch()} disabled={query.isFetching}>{uk ? "Повторити завантаження карток" : "Повторить загрузку карточек"}</button></p>)}
    {shown.length < filtered.length && <button className="button auction-report-more" onClick={() => setPage({ filter, visible: visible + 20 })}>{uk ? "Показати ще" : "Показать ещё"} · {shown.length} / {filtered.length}</button>}
  </section>;
}

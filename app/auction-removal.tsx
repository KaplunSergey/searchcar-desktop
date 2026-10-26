import { useEffect, useRef, type RefObject } from "react";
import type { Locale } from "./translations";

export type AuctionRemovalTarget = { projectId: number; carId: number; title: string; projectName: string };

export function AuctionRemovalDialog({ target, locale, pending, failed, confirm, close, returnFocus, fallbackFocus }: {
  target: AuctionRemovalTarget; locale: Locale; pending: boolean; failed: boolean;
  confirm: () => void; close: () => void;
  returnFocus: RefObject<HTMLButtonElement | null>; fallbackFocus: RefObject<HTMLHeadingElement | null>;
}) {
  const uk = locale === "uk", dialog = useRef<HTMLDialogElement>(null), error = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    return () => {
      node?.close();
      if (returnFocus.current?.isConnected) returnFocus.current.focus();
      else fallbackFocus.current?.focus();
    };
  }, [returnFocus, fallbackFocus]);
  useEffect(() => { if (failed) error.current?.focus(); }, [failed]);
  return <dialog ref={dialog} className="launch-dialog auction-removal-dialog" aria-labelledby="auction-removal-title" aria-describedby="auction-removal-help"
    onCancel={(event) => { event.preventDefault(); if (!pending) close(); }} aria-busy={pending}>
    <h2 id="auction-removal-title">{uk ? "Видалити автомобіль із проєкту?" : "Удалить автомобиль из проекта?"}</h2>
    <p className="auction-removal-target"><b>{target.title}</b><br />{uk ? "Проєкт" : "Проект"}: {target.projectName}</p>
    <p id="auction-removal-help">{uk
      ? "Автомобіль зникне з цього проєкту й більше не додаватиметься до нього під час оновлень. В інших проєктах він залишиться."
      : "Автомобиль исчезнет из этого проекта и больше не будет добавляться в него при обновлениях. В других проектах он останется."}</p>
    {failed && <p ref={error} tabIndex={-1} className="launch-error" role="alert">{uk
      ? "Не вдалося підтвердити видалення. Спробуйте ще раз або оновіть список."
      : "Не удалось подтвердить удаление. Попробуйте снова или обновите список."}</p>}
    <div className="launch-actions">
      <button type="button" className="button" autoFocus disabled={pending} onClick={close}>{uk ? "Скасувати" : "Отмена"}</button>
      <button type="button" className="button danger" disabled={pending} onClick={confirm}>{pending
        ? (uk ? "Видалення…" : "Удаление…") : (uk ? "Видалити з проєкту" : "Удалить из проекта")}</button>
    </div>
  </dialog>;
}

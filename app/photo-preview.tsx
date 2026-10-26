import { useEffect, useRef, useState } from "react";
import type { Locale } from "./translations";

export function PhotoPreview({ src, title, locale }: { src: string; title: string; locale: Locale }) {
  return <PhotoPreviewImage key={src} src={src} title={title} locale={locale} />;
}

function PhotoPreviewImage({ src, title, locale }: { src: string; title: string; locale: Locale }) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const uk = locale === "uk";
  useEffect(() => {
    if (!open) return;
    const node = dialog.current;
    const button = trigger.current;
    node?.showModal();
    return () => { node?.close(); if (button?.isConnected) button.focus(); };
  }, [open]);
  return <>
    <button ref={trigger} type="button" className="auction-photo auction-photo-button" aria-haspopup="dialog"
      aria-label={`${uk ? "Переглянути фото" : "Посмотреть фото"}: ${title}`} onClick={() => { setFailed(false); setOpen(true); }}>
      <img src={src} alt={title} loading="lazy" />
    </button>
    {open && <dialog ref={dialog} className="lightbox auction-image-preview" aria-label={uk ? "Фото автомобіля" : "Фото автомобиля"}
      onCancel={(event) => { event.preventDefault(); setOpen(false); }} onClose={() => setOpen(false)}
      onClick={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
      <div className="lightbox-controls"><button type="button" autoFocus onClick={() => setOpen(false)}>{uk ? "Закрити" : "Закрыть"} ×</button></div>
      {failed ? <p role="alert">{uk ? "Не вдалося завантажити фото" : "Не удалось загрузить фото"}</p> : <img src={src} alt={title} onError={() => setFailed(true)} />}
    </dialog>}
  </>;
}

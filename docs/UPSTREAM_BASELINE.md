# Источник desktop-репозитория

Новый репозиторий создан 2 августа 2026 года как безопасная копия
отслеживаемого кода работающего web-проекта.

Текущее название продукта — `SearchCar Desktop`, локальная папка —
`searchcar-desktop`.

## Источник

- исходная папка: `files-mentioned-by-the-user-monitor`;
- исходный remote: `https://github.com/KaplunSergey/SearchCar.git`;
- исходная ветка: `main`;
- исходный commit:
  `5d4f54bb55be0c4cca9e947bec2c6c5571e74716`;
- состояние исходного worktree при копировании: clean и синхронизировано с
  `origin/main`;
- количество перенесённых tracked files: 75;
- baseline commit нового репозитория:
  `9a9bc29` (`chore: import SearchCar web baseline`).

## Метод копирования

Использован Git archive исходного `HEAD`, затем создан новый независимый `.git`.
История и remotes исходного репозитория не наследовались.

Не переносились `.env`, `.git`, `node_modules`, `.wrangler`, `dist`, `storage`,
`work`, `outputs`, `legacy-data`, Python caches и Docker/PostgreSQL volume.
Благодаря этому новый репозиторий содержит код и шаблоны конфигурации, но не
содержит пользовательские данные или локальные секреты.

## Проверка безопасности

- submodules отсутствуют;
- tracked symlinks отсутствуют;
- PostgreSQL data находится в Docker volume вне Git;
- явные приватные ключи и GitHub tokens среди перенесённых tracked files не
  обнаружены;
- `.env.example` содержит только шаблонные значения;
- runtime data desktop-версии должна всегда находиться в каталоге приложения
  операционной системы, а не внутри репозитория.

## Правило синхронизации

Desktop-изменения не вносятся обратно в исходный web-репозиторий автоматически.
Если в web-версии появляется нужное исправление, оно переносится отдельным
reviewed commit/cherry-pick с обязательным запуском desktop regression tests.

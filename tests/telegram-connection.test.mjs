import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const { TelegramConnection, TELEGRAM_CONNECTION_QUERY, telegramConnectionError } = await import('../app/telegram-connection.tsx');
const { TelegramSharedSetup } = await import('../app/telegram-shared-setup.tsx');

for(const locale of ['ru','uk']) {
  test(`${locale}: shared bot is the default setup, with no token field, delay warning and private-message instructions`,()=>{
    const html=renderToStaticMarkup(React.createElement(TelegramSharedSetup,{locale}));
    assert.doesNotMatch(html,/<input|BotFather|type="password"/);
    assert.match(html,/href="https:\/\/t\.me\/search_car_app_bot"/);
    assert.match(html,/@search_car_app_bot/);
    assert.match(html,locale==='ru'?/Это обычная ссылка на бота/:/Це звичайне посилання на бота/);
    assert.match(html,locale==='ru'?/Выберите проект ниже/:/Виберіть проєкт нижче/);
    assert.match(html,/Start/);
    assert.match(html,locale==='ru'?/Общий бот SearchCar/:/Спільний бот SearchCar/);
    assert.match(html,locale==='ru'?/отправка может задерживаться/:/надсилання може затримуватися/);
    assert.match(html,locale==='ru'?/Скопировать ссылку/:/Скопіювати посилання/);
    assert.match(html,locale==='ru'?/перешлите её нужному человеку/:/перешліть його потрібній людині/);
    assert.match(html,locale==='ru'?/Группы и каналы/:/Групи та канали/);
    assert.match(html,locale==='ru'?/подключение групп и каналов ещё не включено/:/підключення груп і каналів ще не ввімкнено/);
    assert.match(html,locale==='ru'?/не после поиска автоматически/:/не після пошуку автоматично/);
    assert.match(html,locale==='ru'?/за каждым проектом/:/за кожним проєктом/);
  });
}

for (const locale of ['ru','uk']) {
  test(`${locale}: settings offer a masked non-persisted credential, honest connection state and no sending`,()=>{
    const client=new QueryClient();
    client.setQueryData(TELEGRAM_CONNECTION_QUERY,{connected:false,bot:null,channels:[],delivery_enabled:false});
    try {
      let html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramConnection,{locale,request:async()=>{throw Error('no network during render');}})));
      assert.match(html,/type="password"/);assert.match(html,/autoComplete="new-password"/);assert.match(html,/Keychain/);assert.match(html,/DPAPI/);
      assert.doesNotMatch(html,/sendMessage|sendPhoto|Отправить|Надіслати/);
      assert.match(html,locale==='ru'?/Бот не подключён/:/Бота не підключено/);
      client.setQueryData(TELEGRAM_CONNECTION_QUERY,{connected:true,bot:{id:123,username:'fixture_bot'},channels:[{chat_id:-100123,title:'Channel <script>',checked_at:'2026-10-21T10:00:00Z'}],delivery_enabled:false});
      html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramConnection,{locale,request:async()=>{}})));
      assert.match(html,/@fixture_bot/);assert.match(html,/Channel &lt;script&gt;/);assert.match(html,/-100123/);
      assert.match(html,locale==='ru'?/Проверить канал/:/Перевірити канал/);
      assert.match(html,locale==='ru'?/Отключить бота/:/Відключити бота/);
      assert.doesNotMatch(html,/type="password"[^>]*value=/);
    } finally { client.clear(); }
  });
}
test('errors are translated from allowlisted codes, never remote descriptions or arbitrary secrets',()=>{
  for(const locale of ['ru','uk']){
    assert.match(telegramConnectionError({code:'telegram_post_permission_required'},locale),/прав/);
    assert.match(telegramConnectionError({code:'telegram_secure_storage_unavailable'},locale),/файл/);
    assert.doesNotMatch(telegramConnectionError({code:'private-token',message:'another-secret'},locale),/private-token|another-secret/);
  }
});

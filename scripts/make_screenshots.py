"""Сбор модальности «изображения»: скриншоты воспроизведённых дефектов.

Запуск:
    python scripts/make_screenshots.py --out data/images/screenshots

Скриншоты баг-репортов из публичных трекеров хранятся во внешних хранилищах
(GitHub user-attachments, Bugzilla) и не распространяются вместе с датасетами,
поэтому изображения собраны так, как это делает QA-инженер: дефект реально
воспроизводится на тестовом стенде и фиксируется скриншотом браузера.

Тестовый стенд — вымышленный сервис «TaskFlow» (локальный HTTP/HTTPS-сервер в
этом скрипте). Всё, что видно на скриншотах, — настоящее поведение программ:
- страницы ошибок Chromium (DNS, отказ соединения, недоверенный сертификат);
- настоящие трейсбеки Python и Node.js, полученные запуском ошибочного кода;
- настоящие исключения SQLite (блокировка, нарушение UNIQUE, нет таблицы);
- настоящее выполнение XSS-внедрения, mojibake при неверной кодировке,
  NaN из-за ошибки разбора суммы, двойное списание при двойном клике,
  рост JS-кучи при утечке памяти и т.д.

Требуется: pip install playwright && playwright install chromium
"""
import argparse
import asyncio
import csv
import datetime as dt
import html
import json
import os
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

HOST = "127.0.0.1"
HTTP_PORT = 8765
HTTPS_SELF_PORT = 8443
HTTPS_EXPIRED_PORT = 8444
CLOSED_PORT = 59999  # заведомо закрытый порт
TMP = Path(tempfile.mkdtemp(prefix="taskflow_"))

# ---------------------------------------------------------------- общий стиль стенда
CSS = """
*{box-sizing:border-box} body{margin:0;font-family:"DejaVu Sans",Arial,sans-serif;background:#f4f6fa;color:#1d2433}
header{background:#24345a;color:#fff;padding:14px 28px;display:flex;align-items:center;gap:24px}
header b{font-size:20px} header nav a{color:#cbd6f2;margin-right:18px;text-decoration:none;font-size:14px}
main{max-width:980px;margin:28px auto;padding:0 20px}
.card{background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.12);padding:24px;margin-bottom:18px}
h1{font-size:24px;margin:0 0 14px} h2{font-size:18px;margin:0 0 10px}
label{display:block;font-size:13px;color:#5a6478;margin:12px 0 4px}
input{width:100%;padding:10px;border:1px solid #c5cbd8;border-radius:6px;font-size:15px}
button{background:#3366ff;color:#fff;border:0;border-radius:6px;padding:11px 20px;font-size:15px;cursor:pointer}
.err{background:#fdecec;border:1px solid #f3b4b4;color:#a12020;padding:12px 14px;border-radius:6px;margin:12px 0;font-size:14px}
.ok{background:#e8f7ee;border:1px solid #a7dcbc;color:#16653a;padding:12px 14px;border-radius:6px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:14px} td,th{border-bottom:1px solid #e3e6ee;padding:9px;text-align:left}
code,pre{font-family:"DejaVu Sans Mono",monospace}
"""


def page(title, body, extra_head=""):
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{title}</title>
<style>{CSS}</style>{extra_head}</head><body>
<header><b>TaskFlow</b><nav><a href="#">Задачи</a><a href="#">Проекты</a><a href="#">Отчёты</a><a href="#">Тариф</a></nav></header>
<main>{body}</main></body></html>"""


def debug_500(exc_text, title, path):
    """Отладочная страница 500, как в dev-режиме веб-фреймворков."""
    last = exc_text.strip().splitlines()[-1]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;font-family:"DejaVu Sans",Arial;background:#fff}} .top{{background:#ffc;border-bottom:1px solid #ddd;padding:18px 26px}}
h1{{margin:0 0 6px;font-size:24px;color:#a00}} .meta{{font-size:13px;color:#555}}
pre{{background:#f6f6f6;margin:20px 26px;padding:16px;border-left:4px solid #c00;font:13px/1.5 "DejaVu Sans Mono",monospace;white-space:pre-wrap}}
</style></head><body><div class="top"><h1>{html.escape(title)}</h1>
<div class="meta">Request URL: http://{HOST}:{HTTP_PORT}{html.escape(path)} &nbsp;|&nbsp; Status: 500 Internal Server Error
&nbsp;|&nbsp; Exception: <b>{html.escape(last)}</b></div></div>
<pre>{html.escape(exc_text)}</pre></body></html>"""


# ---------------------------------------------------------------- воспроизведение ошибок БД
def db_locked():
    db = TMP / "orders.db"
    a = sqlite3.connect(db, timeout=0.1)
    a.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, status TEXT)")
    a.commit()
    a.execute("BEGIN EXCLUSIVE")  # фоновая задача держит эксклюзивную блокировку
    b = sqlite3.connect(db, timeout=0.5)
    try:
        b.execute("UPDATE orders SET status='paid' WHERE id=?", (42,))
    finally:
        a.rollback()


def db_unique():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL)")
    c.execute("INSERT INTO users(email) VALUES ('ivan@taskflow.example')")
    c.execute("INSERT INTO users(email) VALUES ('ivan@taskflow.example')")


def db_missing():
    c = sqlite3.connect(":memory:")
    c.execute("SELECT id, total FROM invoices WHERE customer_id = ?", (7,)).fetchall()


def handler_crash():
    def get_user(uid):
        return None  # пользователь не найден, но вызывающий код это не проверяет
    user = get_user(1001)
    return user.profile["display_name"]


CHARGES = []

# ---------------------------------------------------------------- HTTP-сервер стенда
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8")
        u = urlparse(self.path)
        if u.path == "/login":
            f = parse_qs(raw)
            login = html.escape(f.get("login", [""])[0])
            self.send(401, page("Вход", f"""<div class="card" style="max-width:420px;margin:40px auto">
<h1>Вход в TaskFlow</h1><div class="err"><b>401 Unauthorized.</b> Неверный логин или пароль.
Осталось попыток: 2. После 3 неудачных попыток учётная запись будет заблокирована на 15 минут.</div>
<form method="post" action="/login"><label>Логин</label><input name="login" value="{login}">
<label>Пароль</label><input name="password" type="password"><br><br><button>Войти</button></form></div>"""))
        elif u.path == "/api/payments":
            CHARGES.append({"id": f"ch_{len(CHARGES)+1:04d}", "amount": "1 299,00 ₽",
                            "at": dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]})
            body = json.loads(raw or "{}")
            if body.get("card", "").endswith("0002"):
                self.send(402, json.dumps({"error": "card_declined", "decline_code": "insufficient_funds",
                                           "message": "Your card has insufficient funds."}),
                          "application/json")
            else:
                time.sleep(0.4)  # платёжный шлюз отвечает не мгновенно
                self.send(200, json.dumps({"status": "succeeded", "charge": CHARGES[-1]}), "application/json")
        else:
            self.send(404, "not found", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        routes = {
            "/login": self.r_login, "/api/me": self.r_api_me, "/admin/users": self.r_admin,
            "/checkout": self.r_checkout, "/invoice": self.r_invoice, "/double": self.r_double,
            "/api/charges": self.r_charges,
            "/crash": self.r_crash, "/db/locked": self.r_db(db_locked, "OperationalError at /orders/42/pay"),
            "/db/unique": self.r_db(db_unique, "IntegrityError at /signup"),
            "/db/missing": self.r_db(db_missing, "OperationalError at /billing/invoices"),
            "/slow": self.r_slow, "/api/slow": self.r_api_slow, "/longtask": self.r_longtask,
            "/latency": self.r_latency, "/api/search": self.r_api_search,
            "/leak": self.r_leak, "/mojibake": self.r_mojibake, "/i18n": self.r_i18n,
            "/formats": self.r_formats, "/search": self.r_search, "/offline": self.r_offline,
            "/layout/overflow": self.r_overflow, "/layout/overlap": self.r_overlap,
            "/layout/modal": self.r_modal, "/api/invoices/43": self.r_idor,
        }
        fn = routes.get(p)
        if fn:
            fn(q)
        else:
            self.send(404, page("404", "<div class='card'><h1>404</h1></div>"))

    # --- аутентификация
    def r_login(self, q):
        self.send(200, page("Вход", """<div class="card" style="max-width:420px;margin:40px auto">
<h1>Вход в TaskFlow</h1><form method="post" action="/login"><label>Логин</label><input name="login">
<label>Пароль</label><input name="password" type="password"><br><br><button id="go">Войти</button></form></div>"""))

    def r_api_me(self, q):
        self.send(401, json.dumps({"error": "invalid_token", "error_description":
                                   "The access token expired at 2026-10-17T09:12:44Z",
                                   "request_id": "req_7f3a91c2"}, indent=2), "application/json",
                  {"WWW-Authenticate": 'Bearer realm="taskflow", error="invalid_token"'})

    def r_admin(self, q):
        self.send(403, page("403", """<div class="card"><h1>403 Forbidden</h1>
<div class="err">У пользователя <b>ivan.petrov</b> (роль <code>project_admin</code>) нет права
<code>users:read</code>. Роль была назначена 5 минут назад, но токен доступа ещё содержит старый набор прав
(scope: tasks:read tasks:write).</div><p>Обратитесь к администратору организации.</p></div>"""))

    # --- оплата
    def r_checkout(self, q):
        self.send(200, page("Оплата", """<div class="card" style="max-width:520px"><h1>Оплата тарифа «Команда»</h1>
<p>К оплате: <b>1 299,00 ₽</b> / месяц</p><label>Номер карты</label><input id="card" value="4000 0000 0000 0002">
<label>Срок / CVC</label><input value="12/28   •••"><br><br><button id="pay">Оплатить</button>
<div id="res"></div></div><script>
document.getElementById('pay').onclick=async()=>{const r=await fetch('/api/payments',{method:'POST',
body:JSON.stringify({card:document.getElementById('card').value.replace(/ /g,'')})});const j=await r.json();
document.getElementById('res').innerHTML='<div class="err"><b>Платёж отклонён ('+r.status+' '+j.error+
')</b><br>decline_code: '+j.decline_code+'<br>'+j.message+'</div>';};</script>"""))

    def r_invoice(self, q):
        self.send(200, page("Счёт", """<div class="card"><h1>Счёт № INV-2026-0042</h1><table id="t">
<tr><th>Позиция</th><th>Кол-во</th><th>Цена</th><th>Сумма</th></tr></table>
<p style="font-size:20px">Итого к оплате: <b id="total"></b></p><p style="color:#888;font-size:12px">
Цены приходят из API в формате локали ru-RU</p></div><script>
const items=[["Тариф «Команда», 5 мест","5","1 299,00"],["Доп. хранилище 100 ГБ","1","349,50"],["Интеграция с Jira","1","0,00"]];
let total=0;const t=document.getElementById('t');
for(const [n,qty,price] of items){const sum=parseFloat(price)*parseInt(qty); // ошибка: parseFloat("1 299,00") -> 1
 total+=sum; t.insertAdjacentHTML('beforeend',`<tr><td>${n}</td><td>${qty}</td><td>${price} ₽</td><td>${sum.toFixed(2)} ₽</td></tr>`);}
total+=parseFloat(undefined); // скидка не пришла из API
document.getElementById('total').textContent=total.toFixed(2)+' ₽';</script>"""))

    def r_double(self, q):
        self.send(200, page("Оплата", """<div class="card" style="max-width:620px"><h1>Продление подписки</h1>
<p>Сумма: <b>1 299,00 ₽</b></p><button id="pay">Оплатить</button><div id="st"></div>
<h2 style="margin-top:22px">Списания по карте •••• 4242</h2><table id="ch"><tr><th>ID</th><th>Сумма</th><th>Время</th></tr></table>
</div><script>
const pay=async()=>{await fetch('/api/payments',{method:'POST',body:JSON.stringify({card:'4242'})});
 const r=await (await fetch('/api/charges')).json(); const t=document.getElementById('ch');
 t.querySelectorAll('tr.r').forEach(e=>e.remove());
 r.forEach(c=>t.insertAdjacentHTML('beforeend',`<tr class="r"><td>${c.id}</td><td>${c.amount}</td><td>${c.at}</td></tr>`));
 document.getElementById('st').innerHTML=r.length>1?'<div class="err">Подписка продлена, но с карты списано '+r.length+' раза</div>':'';};
document.getElementById('pay').onclick=pay; // кнопка не блокируется на время запроса</script>"""))

    def r_charges(self, q):
        self.send(200, json.dumps(CHARGES), "application/json")

    # --- падения и БД
    def r_crash(self, q):
        try:
            handler_crash()
        except Exception:
            self.send(500, debug_500(traceback.format_exc(), "AttributeError at /profile/1001", "/profile/1001"))

    def r_db(self, fn, title):
        def h(q):
            try:
                fn()
            except Exception:
                self.send(500, debug_500(traceback.format_exc(), title, self.path))
        return h

    # --- производительность
    def r_slow(self, q):
        self.send(200, page("Отчёт", """<div class="card"><h1>Отчёт по задачам за квартал</h1>
<div style="display:flex;align-items:center;gap:14px;margin:30px 0"><div style="width:34px;height:34px;border:4px solid #c9d4f5;
border-top-color:#3366ff;border-radius:50%;animation:s 1s linear infinite"></div><div>Загрузка данных… прошло <b id="sec">0.0</b> с</div></div>
<div id="d" style="color:#888">Запрос GET /api/slow ещё выполняется</div></div>
<style>@keyframes s{to{transform:rotate(360deg)}}</style><script>
const t0=performance.now();setInterval(()=>document.getElementById('sec').textContent=((performance.now()-t0)/1000).toFixed(1),100);
fetch('/api/slow').then(r=>r.json()).then(j=>document.getElementById('d').textContent=JSON.stringify(j));</script>"""))

    def r_api_slow(self, q):
        time.sleep(30)
        self.send(200, "{}", "application/json")

    def r_api_search(self, q):
        n = int(q.get("n", ["10"])[0])
        time.sleep(n * n / 4_000_000)  # квадратичная сложность поиска
        self.send(200, json.dumps({"n": n}), "application/json")

    def r_latency(self, q):
        self.send(200, page("Поиск", """<div class="card"><h1>Время ответа поиска в зависимости от числа задач</h1>
<table id="t"><tr><th>Задач в проекте</th><th>Время ответа, мс</th><th></th></tr></table>
<p id="done" style="color:#888"></p></div><script>
(async()=>{const t=document.getElementById('t');for(const n of [100,500,1000,2000,3000,4000]){const a=performance.now();
await fetch('/api/search?n='+n);const ms=performance.now()-a;
t.insertAdjacentHTML('beforeend',`<tr><td>${n}</td><td>${ms.toFixed(0)}</td><td><div style="height:12px;background:${ms>1000?'#d33':'#3366ff'};width:${Math.min(ms/8,520)}px"></div></td></tr>`);}
document.getElementById('done').textContent='Измерено в браузере (fetch, performance.now). SLA: 300 мс.';})();</script>"""))

    def r_longtask(self, q):
        self.send(200, page("Канбан", """<div class="card"><h1>Канбан-доска: перетаскивание карточки</h1>
<div id="o"></div></div><script>
const o=document.getElementById('o');const tasks=[];
new PerformanceObserver(l=>{for(const e of l.getEntries()){o.insertAdjacentHTML('beforeend',
`<div class="err">Long Task: главный поток заблокирован на <b>${e.duration.toFixed(0)} мс</b> (startTime ${e.startTime.toFixed(0)} мс). Интерфейс не отвечал на ввод.</div>`);}}).observe({type:'longtask',buffered:true});
setTimeout(()=>{const a=performance.now();
 // ошибка: при каждом перемещении карточки пересортировка всех задач с полным перебором
 const arr=Array.from({length:20000},(_, i)=>({id:i,p:Math.random()}));
 for(let i=0;i<arr.length;i++)for(let j=0;j<arr.length;j++)if(arr[i].p<arr[j].p){const t=arr[i];arr[i]=arr[j];arr[j]=t;}
 o.insertAdjacentHTML('afterbegin',`<p>Перерисовка 20000 карточек заняла ${(performance.now()-a).toFixed(0)} мс</p>`);},100);</script>"""))

    # --- память
    def r_leak(self, q):
        self.send(200, page("Утечка", """<div class="card"><h1>Размер JS-кучи вкладки «Лента активности»</h1>
<svg id="g" width="900" height="300" style="background:#fafbfe;border:1px solid #e3e6ee"></svg>
<p id="s"></p></div><script>
window.__cache=[];const pts=[];const g=document.getElementById('g');
function tick(){ // ошибка: каждое обновление ленты сохраняет все DOM-узлы и данные в глобальный кэш
 const n=[];for(let i=0;i<4000;i++){const d=document.createElement('div');d.textContent='event '+i+' '+Math.random();n.push({d,payload:new Array(50).fill(i)});}
 window.__cache.push(n);pts.push(performance.memory.usedJSHeapSize/1048576);
 const max=Math.max(...pts)*1.1;g.innerHTML='<polyline fill="none" stroke="#d33" stroke-width="3" points="'+pts.map((v,i)=>`${20+i*28},${290-v/max*270}`).join(' ')+'"/>'+
 `<text x="24" y="20" font-size="13">${max.toFixed(0)} МБ</text><text x="24" y="296" font-size="13">0</text>`;
 document.getElementById('s').innerHTML=`Обновлений ленты: <b>${pts.length}</b>, used JS heap: <b>${pts.at(-1).toFixed(1)} МБ</b> (было ${pts[0].toFixed(1)} МБ)`;}
const iv=setInterval(()=>{tick();if(pts.length>=31)clearInterval(iv);},60);</script>"""))

    # --- локализация
    def r_mojibake(self, q):
        body = page("Уведомления", """<div class="card"><h1>Уведомления</h1>
<p>Задача «Исправить расчёт НДС» назначена на вас.</p><p>Срок выполнения: 20 октября 2026 г.</p>
<p>Комментарий от Анны Смирновой: «Проверьте, пожалуйста, отчёт за сентябрь».</p></div>""")
        # ошибка: тело в UTF-8, а в заголовке указана windows-1251
        self.send(200, body.encode("utf-8"), "text/html; charset=windows-1251")

    def r_i18n(self, q):
        self.send(200, page("Настройки", """<div class="card"><h1 id="h"></h1><div id="f"></div></div><script>
const ru={"settings.title":"Настройки профиля","settings.name":"Имя","settings.email":"Электронная почта"};
const t=k=>ru[k]??k; // при отсутствии перевода показывается ключ
document.getElementById('h').textContent=t('settings.title');
for(const k of ['settings.name','settings.email','settings.timezone','settings.notifications.digest','settings.language'])
 document.getElementById('f').insertAdjacentHTML('beforeend',`<label>${t(k)}</label><input value="">`);
document.getElementById('f').insertAdjacentHTML('beforeend','<br><br><button>'+t('common.actions.save')+'</button> <button style="background:#888">'+t('common.actions.cancel')+'</button>');</script>"""))

    def r_formats(self, q):
        self.send(200, page("Отчёт", """<div class="card"><h1>Финансовый отчёт (интерфейс: русский)</h1><table id="t">
<tr><th>Показатель</th><th>Отображается</th><th>Ожидается (ru-RU)</th></tr></table></div><script>
const d=new Date(2026,9,17,14,5),n=1234567.891;const rows=[
['Дата отчёта',d.toLocaleDateString('en-US'),d.toLocaleDateString('ru-RU')],
['Время',d.toLocaleTimeString('en-US'),d.toLocaleTimeString('ru-RU')],
['Выручка',n.toLocaleString('en-US',{style:'currency',currency:'USD'}),n.toLocaleString('ru-RU',{style:'currency',currency:'RUB'})],
['Доля',(0.153).toLocaleString('en-US',{style:'percent',minimumFractionDigits:1}),(0.153).toLocaleString('ru-RU',{style:'percent',minimumFractionDigits:1})],
['Задач закрыто','1 tasks','1 задача'],['Задач открыто','5 task','5 задач']];
for(const r of rows)document.getElementById('t').insertAdjacentHTML('beforeend',`<tr><td>${r[0]}</td><td style="color:#a12020">${r[1]}</td><td>${r[2]}</td></tr>`);</script>"""))

    # --- безопасность
    def r_search(self, q):
        term = q.get("q", [""])[0]
        # ошибка: поисковый запрос вставляется в страницу без экранирования
        self.send(200, page("Поиск", f"""<div class="card"><h1>Поиск задач</h1>
<form><input name="q" value=""></form><p>Результаты по запросу: {term}</p><p>Ничего не найдено.</p></div>""",
                            "<script>document.cookie='session=8f2c1e9ab47d; path=/'</script>"))

    def r_idor(self, q):
        # ошибка: проверяется только наличие сессии, но не владелец счёта
        self.send(200, json.dumps({"request": {"session_user_id": 1001, "path": "/api/invoices/43"},
                                   "invoice": {"id": 43, "customer_id": 2002, "customer_name": "ООО «Северный ветер»",
                                               "email": "finance@north-wind.example", "total": "48 900,00 ₽",
                                               "card_last4": "8812", "billing_address": "г. Казань, ул. Примерная, д. 1"}},
                                  ensure_ascii=False, indent=2), "application/json; charset=utf-8")

    # --- сеть
    def r_offline(self, q):
        self.send(200, page("Синхронизация", f"""<div class="card"><h1>Синхронизация задач</h1>
<p>Сервис синхронизации: <code>http://{HOST}:{CLOSED_PORT}/sync</code></p><div id="log"></div></div><script>
(async()=>{{for(let i=1;i<=3;i++){{const a=performance.now();try{{await fetch('http://{HOST}:{CLOSED_PORT}/sync');}}
catch(e){{document.getElementById('log').insertAdjacentHTML('beforeend',`<div class="err">Попытка ${{i}}/3: <b>${{e.name}}: ${{e.message}}</b> (${{(performance.now()-a).toFixed(0)}} мс)</div>`);}}}}
document.getElementById('log').insertAdjacentHTML('beforeend','<div class="err">Изменения не сохранены. Последняя успешная синхронизация: 2 часа назад.</div>');}})();</script>"""))

    # --- вёрстка
    def r_overflow(self, q):
        self.send(200, page("Задача", """<div class="card" style="width:340px"><h2>Задача TF-1287</h2>
<div style="display:flex;gap:8px;width:300px">
<button style="white-space:nowrap;width:140px">Отправить на согласование</button>
<button style="white-space:nowrap;width:140px;background:#e0e4ee;color:#1d2433">Сохранить черновик</button></div>
<p style="width:300px;white-space:nowrap;overflow:hidden">Описание: Пользователь не может прикрепить файл размером больше 10 МБ к задаче</p>
<div style="width:300px;border:1px solid #ddd;padding:6px">Исполнитель: Константинопольский-Великорецкий Александр Владимирович</div></div>"""))

    def r_overlap(self, q):
        self.send(200, page("Проект", """<div class="card"><h1>Проект «Мобильное приложение»</h1>
<p>Прогресс: 64% · 128 задач · 9 участников</p>
<div style="position:absolute;top:150px;left:20px;background:#ffe9a8;padding:12px;width:330px;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,.2)">
Cookie: мы используем файлы cookie для аналитики. <button style="padding:6px 10px">ОК</button></div>
<div style="display:flex;gap:10px;margin-top:12px"><button>Новая задача</button><button>Фильтры</button><button>Экспорт</button></div>
<p style="margin-top:20px">Последние изменения: Анна закрыла задачу TF-1290, Олег добавил комментарий к TF-1288.</p></div>"""))

    def r_modal(self, q):
        self.send(200, page("Удаление", """<div class="card" style="position:relative;z-index:10"><h1>Проект «Сайт компании»</h1>
<p>Нажмите «Удалить проект», чтобы удалить проект и все задачи.</p><button style="background:#c33">Удалить проект</button></div>
<div style="position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:20"></div>
<div style="position:fixed;top:220px;left:50%;transform:translateX(-50%);width:420px;background:#fff;border-radius:10px;padding:22px;z-index:5;box-shadow:0 4px 18px rgba(0,0,0,.3)">
<h2>Удалить проект?</h2><p>Это действие нельзя отменить. Будут удалены 214 задач.</p>
<button style="background:#c33">Удалить</button> <button style="background:#888">Отмена</button></div>"""))


def make_cert(cn, expired=False):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TaskFlow test stand")])
    now = dt.datetime.now(dt.timezone.utc)
    nb, na = (now - dt.timedelta(days=400), now - dt.timedelta(days=35)) if expired else (now, now + dt.timedelta(days=30))
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(nb).not_valid_after(na)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
            .sign(key, hashes.SHA256()))
    c, k = TMP / f"{cn}.crt", TMP / f"{cn}.key"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    return c, k


def serve(port, tls=None):
    srv = ThreadingHTTPServer((HOST, port), Handler)
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*tls)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- «терминальные» скриншоты
SCRIPTS = {
    "order_service.py": '''import json

def load_order(order_id):
    orders = json.loads('{"41": {"items": [{"sku": "A1", "qty": 2}]}}')
    return orders.get(str(order_id))

def order_total(order_id):
    order = load_order(order_id)
    return sum(i["qty"] for i in order["items"])

print("Заказ 41:", order_total(41))
print("Заказ 42:", order_total(42))
''',
    "report_builder.py": '''import resource
resource.setrlimit(resource.RLIMIT_AS, (400 * 2**20, 400 * 2**20))

def build_report(rows):
    # ошибка: весь журнал событий загружается в память целиком и дублируется
    cache = [b"event"] * rows * 40
    return len(cache)

print("Строим отчёт за 2 года...")
print(build_report(50_000_000))
''',
    "sync_worker.js": '''const tasks = [{ id: 1, assignee: { name: "Анна" } }, { id: 2 }];
for (const t of tasks) {
  console.log(`Задача ${t.id}: исполнитель ${t.assignee.name}`);
}
''',
    "export_worker.js": '''// ошибка: результаты экспорта накапливаются в массиве и никогда не освобождаются
const rows = [];
let i = 0;
setInterval(() => {}, 1000);
while (true) {
  rows.push({ id: i++, payload: "x".repeat(1024) });
}
''',
}


def run_cmd(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return (p.stdout + p.stderr).rstrip(), p.returncode


def terminal_html(prompt_cmd, output, code):
    lines = output.splitlines()
    if len(lines) > 34:
        lines = lines[:34] + ["    ..."]
    body = html.escape("\n".join(lines))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#1e1e1e}} .bar{{background:#323233;color:#ccc;font:13px "DejaVu Sans";padding:8px 14px}}
.bar span{{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:6px;vertical-align:middle}}
pre{{margin:0;padding:16px 18px;color:#d4d4d4;font:14px/1.45 "DejaVu Sans Mono",monospace;white-space:pre-wrap}}
.p{{color:#6a9955}} .c{{color:#f48771}}</style></head><body>
<div class="bar"><span style="background:#ff5f56"></span><span style="background:#ffbd2e"></span><span style="background:#27c93f"></span>
&nbsp; qa@taskflow-stage: ~/services</div>
<pre><span class="p">qa@taskflow-stage:~/services$</span> {html.escape(prompt_cmd)}
{body}
<span class="c">[exit code {code}]</span>
<span class="p">qa@taskflow-stage:~/services$</span> </pre></body></html>"""


# ---------------------------------------------------------------- список скриншотов
B = f"http://{HOST}:{HTTP_PORT}"
SHOTS = [
    # category, file slug, описание, тип, параметры
    ("crash", "python_typeerror", "Падение сервиса заказов: TypeError 'NoneType' object is not subscriptable при обращении к несуществующему заказу (реальный traceback Python)", "term", {"cmd": "python3 order_service.py", "argv": [sys.executable, "order_service.py"]}),
    ("crash", "node_typeerror", "Падение воркера синхронизации Node.js: TypeError Cannot read properties of undefined", "term", {"cmd": "node sync_worker.js", "argv": ["node", "sync_worker.js"]}),
    ("crash", "http500_debug", "Страница 500 Internal Server Error с трейсбеком: обращение к атрибуту None в обработчике профиля", "url", {"url": B + "/crash"}),
    ("ui_layout", "button_overflow", "Текст кнопок и ФИО не помещаются в контейнер и вылезают за границы карточки (мобильная ширина)", "url", {"url": B + "/layout/overflow", "mobile": True}),
    ("ui_layout", "banner_overlap", "Плашка cookie перекрывает кнопки действий проекта на узком экране", "url", {"url": B + "/layout/overlap", "mobile": True}),
    ("ui_layout", "modal_behind_overlay", "Диалог подтверждения удаления оказался под затемняющим слоем (ошибка z-index), кнопки недоступны", "url", {"url": B + "/layout/modal"}),
    ("authentication", "login_401", "Форма входа после реальной отправки неверного пароля: 401 Unauthorized и предупреждение о блокировке", "login", {"url": B + "/login"}),
    ("authentication", "api_token_expired", "Ответ API 401 invalid_token: истёк токен доступа (JSON в браузере)", "url", {"url": B + "/api/me"}),
    ("authentication", "forbidden_403", "403 Forbidden: новая роль пользователя не попала в выданный ранее токен", "url", {"url": B + "/admin/users"}),
    ("payment", "card_declined_402", "Оплата тарифа: платёжный API вернул 402 card_declined / insufficient_funds", "click", {"url": B + "/checkout", "click": "#pay", "wait": 800}),
    ("payment", "invoice_nan_total", "Счёт: суммы посчитаны неверно (parseFloat('1 299,00') = 1), итог NaN ₽", "url", {"url": B + "/invoice"}),
    ("payment", "double_charge", "Двойное списание: кнопка «Оплатить» не блокируется, двойной клик создаёт два платежа", "dblclick", {"url": B + "/double", "click": "#pay", "wait": 1500}),
    ("database", "sqlite_database_locked", "500: sqlite3.OperationalError database is locked при оплате заказа (параллельная транзакция)", "url", {"url": B + "/db/locked"}),
    ("database", "unique_constraint", "500: sqlite3.IntegrityError UNIQUE constraint failed: users.email при регистрации", "url", {"url": B + "/db/unique"}),
    ("database", "no_such_table", "500: sqlite3.OperationalError no such table: invoices (не применена миграция)", "url", {"url": B + "/db/missing"}),
    ("network", "dns_not_resolved", "Chromium: ERR_NAME_NOT_RESOLVED — адрес API не разрешается DNS", "neterr", {"url": "http://api.taskflow.invalid/v1/tasks"}),
    ("network", "connection_refused", "Chromium: ERR_CONNECTION_REFUSED — сервис не принимает соединения", "neterr", {"url": f"http://{HOST}:{CLOSED_PORT}/"}),
    ("network", "fetch_failed_sync", "Синхронизация задач: три попытки fetch завершились TypeError: Failed to fetch", "url", {"url": B + "/offline", "wait": 1500}),
    ("performance", "spinner_timeout", "Отчёт грузится дольше 8 секунд: бесконечный индикатор загрузки", "url", {"url": B + "/slow", "wait": 8200}),
    ("performance", "search_latency", "Время ответа поиска растёт квадратично с числом задач (измерено в браузере)", "url", {"url": B + "/latency", "wait": 9000}),
    ("performance", "long_task_kanban", "Long Task: главный поток заблокирован на секунды при перетаскивании карточки", "url", {"url": B + "/longtask", "wait": 1500, "until_text": "Перерисовка"}),
    ("memory", "node_heap_oom", "Node.js: FATAL ERROR — JavaScript heap out of memory у воркера экспорта", "term", {"cmd": "node --max-old-space-size=48 export_worker.js", "argv": ["node", "--max-old-space-size=48", "export_worker.js"]}),
    ("memory", "python_memoryerror", "Python: MemoryError при построении отчёта (весь журнал грузится в память)", "term", {"cmd": "python3 report_builder.py", "argv": [sys.executable, "report_builder.py"]}),
    ("memory", "js_heap_growth", "Утечка памяти во вкладке: used JS heap растёт с каждым обновлением ленты", "url", {"url": B + "/leak", "wait": 4000}),
    ("localization", "mojibake_cp1251", "Кракозябры: страница в UTF-8 отдана с заголовком charset=windows-1251", "url", {"url": B + "/mojibake"}),
    ("localization", "missing_translation_keys", "Вместо русских подписей показаны ключи локализации (settings.timezone, common.actions.save)", "url", {"url": B + "/i18n"}),
    ("localization", "wrong_number_date_format", "Даты, валюта и множественное число в формате en-US в русском интерфейсе", "url", {"url": B + "/formats"}),
    ("security", "cert_self_signed", "Chromium: недоверенный самоподписанный сертификат биллинга (страница-предупреждение)", "neterr", {"url": f"https://{HOST}:{HTTPS_SELF_PORT}/checkout"}),
    ("security", "idor_foreign_invoice", "IDOR: пользователь 1001 получает по API чужой счёт (customer_id 2002) с ответом 200 OK", "url", {"url": B + "/api/invoices/43"}),
    ("security", "reflected_xss", "Отражённый XSS в поиске: внедрённый скрипт выполнился и прочитал document.cookie", "url", {"url": B + "/search?q=" + "%3Cimg%20src%3Dx%20onerror%3D%22document.body.insertAdjacentHTML('afterbegin'%2C'%3Cdiv%20style%3D%5C'background%3A%23c00%3Bcolor%3A%23fff%3Bpadding%3A14px%3Bfont%3A16px%20monospace%5C'%3EXSS%20executed%3A%20document.cookie%20%3D%20'%2Bdocument.cookie%2B'%3C%2Fdiv%3E')%22%3E"}),
]


async def shoot(out: Path):
    for name, code in SCRIPTS.items():
        (TMP / name).write_text(code, encoding="utf-8")
    serve(HTTP_PORT)
    serve(HTTPS_SELF_PORT, make_cert(HOST))
    out.mkdir(parents=True, exist_ok=True)
    meta = []
    counters = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chromium", args=[
            "--no-proxy-server", "--enable-precise-memory-info", "--lang=ru-RU", "--explicitly-allowed-ports=59999"])
        for cat, slug, desc, kind, prm in SHOTS:
            counters[cat] = counters.get(cat, 0) + 1
            fname = f"{cat}_{counters[cat]:02d}_{slug}.png"
            mobile = prm.get("mobile", False)
            ctx = await browser.new_context(
                viewport={"width": 390, "height": 760} if mobile else {"width": 1280, "height": 800},
                device_scale_factor=2 if mobile else 1, locale="ru-RU", ignore_https_errors=False)
            pg = await ctx.new_page()
            note = ""
            if kind == "term":
                output, rc = run_cmd(prm["argv"], TMP)
                note = f"exit code {rc}; {output.strip().splitlines()[-1][:120]}"
                await pg.set_viewport_size({"width": 1100, "height": 640})
                await pg.set_content(terminal_html(prm["cmd"], output, rc))
            elif kind == "neterr":
                try:
                    await pg.goto(prm["url"], timeout=10000)
                except Exception as e:
                    note = str(e).split("\n")[0].replace("Page.goto: ", "")
                await pg.wait_for_timeout(700)
            elif kind == "login":
                await pg.goto(prm["url"])
                await pg.fill("input[name=login]", "ivan.petrov")
                await pg.fill("input[name=password]", "wrong-password")
                async with pg.expect_navigation():
                    await pg.click("#go")
                note = "форма отправлена, ответ 401"
            elif kind in ("click", "dblclick"):
                CHARGES.clear()
                await pg.goto(prm["url"])
                if kind == "click":
                    await pg.click(prm["click"])
                else:
                    await pg.dblclick(prm["click"])
                await pg.wait_for_timeout(prm.get("wait", 800))
            else:
                try:
                    resp = await pg.goto(prm["url"], timeout=15000, wait_until="load" if "wait" not in prm else "commit")
                    note = f"HTTP {resp.status}" if resp else ""
                except Exception as e:
                    note = str(e).split("\n")[0]
                if "until_text" in prm:
                    await pg.wait_for_selector(f"text={prm['until_text']}", timeout=60000)
                await pg.wait_for_timeout(prm.get("wait", 400))
            path = out / cat / fname
            path.parent.mkdir(parents=True, exist_ok=True)
            await pg.screenshot(path=str(path))
            await ctx.close()
            meta.append({"file": f"{cat}/{fname}", "category": cat, "description": desc,
                         "how_obtained": "воспроизведение на тестовом стенде, скриншот Chromium (Playwright)",
                         "url_or_command": prm.get("cmd") or prm.get("url"),
                         "viewport": "390x760@2x" if mobile else ("1100x640" if kind == "term" else "1280x800"),
                         "observed": note})
            print("ok", fname, "|", note)
        await browser.close()
    with open(out.parent / "images.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(meta[0].keys()))
        w.writeheader()
        w.writerows(meta)
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/images/screenshots", type=Path)
    a = ap.parse_args()
    asyncio.run(shoot(a.out))

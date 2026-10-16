#!/usr/bin/env python3
"""Ферма вкусов: Python 3.10+, без внешних зависимостей."""
import argparse, getpass, hashlib, hmac, json, os, re, secrets, sqlite3, time
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(mode=0o700, exist_ok=True)
DB = DATA / 'farm.sqlite3'
IDS = ['iron','curd','berry','nutri','omega','sun']
SESSIONS = {}
FAILURES = {}
def connect():
 c=sqlite3.connect(DB, timeout=15)
 c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
 c.execute('CREATE TABLE IF NOT EXISTS families (code TEXT PRIMARY KEY, child TEXT, parent TEXT, updated TEXT)')
 return c

def setting():
 with connect() as c:
  row=c.execute("SELECT value FROM settings WHERE key='password'").fetchone()
  return json.loads(row[0]) if row else None

def set_password():
 password=getpass.getpass('Новый пароль администратора (минимум 12 символов): ')
 if len(password)<12: raise SystemExit('Пароль слишком короткий')
 if password!=getpass.getpass('Повторите пароль: '): raise SystemExit('Пароли не совпадают')
 salt=secrets.token_hex(16)
 value=json.dumps([salt,hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()])
 with connect() as c: c.execute("INSERT OR REPLACE INTO settings VALUES ('password',?)",(value,))
 print('Пароль сохранён. Запустите сервер снова.')

def validate_child(v):
 if v is None:return None
 if not isinstance(v,dict):raise ValueError('Неверная детская анкета')
 ratings=v.get('ratings',{})
 if not isinstance(ratings,dict) or any(k not in IDS or (ratings[k] is not None and (type(ratings[k]) is not int or not 1<=ratings[k]<=5)) for k in ratings):raise ValueError('Неверные оценки')
 ratings={k:ratings.get(k) for k in IDS}
 # The UI sends null for products not rated yet.
 basket=v.get('basket',[])
 if not isinstance(basket,list) or len(set(basket))!=len(basket) or any(k not in IDS for k in basket):raise ValueError('Неверная корзина')
 prices=dict(zip(IDS,[79,99,109,89,95,85]))
 if sum(prices[k] for k in basket)>230:raise ValueError('Превышен бюджет')
 finished=v.get('finished') is True
 if finished and any(ratings[k] is None for k in IDS):raise ValueError('Оцените все продукты')
 ideas=v.get('ideas',[])
 allowed={'base':['Овёс','Творог','Нут','Йогурт','Фруктовое пюре'],'flavor':['Клубника','Курага','Яблоко','Какао','Тыква'],'extra':['Семечки','Лён','Орехи','Смородина','Корица'],'form':['Батончик','Хрустики','Кубики','Мини-кексы']}
 if not isinstance(ideas,list) or len(ideas)>10 or any(not isinstance(a,dict) or any(a.get(k) not in opts for k,opts in allowed.items()) for a in ideas):raise ValueError('Неверная идея')
 return {'ratings':ratings,'basket':basket,'finished':finished,'ideas':[{k:a[k] for k in allowed} for a in ideas],'completedAt':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()) if finished else None}

def validate_parent(v):
 if v is None:return None
 if not isinstance(v,dict):raise ValueError('Неверная родительская анкета')
 choices=v.get('choices',{})
 if not isinstance(choices,dict) or any(choices.get(k) not in ['yes','maybe','no'] for k in IDS):raise ValueError('Ответьте на все шесть вопросов')
 ideas=v.get('ideaChoices',{})
 if not isinstance(ideas,dict) or len(ideas)>10 or any(not re.fullmatch('[0-9]',k) or val not in ['yes','maybe','no'] for k,val in ideas.items()):raise ValueError('Неверные ответы об идеях')
 return {'choices':{k:choices[k] for k in IDS},'ideaChoices':ideas,'submittedAt':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}

class Handler(BaseHTTPRequestHandler):
 def send(self,status,value,ctype='application/json; charset=utf-8',cookie=None):
  body=json.dumps(value,ensure_ascii=False).encode() if ctype.startswith('application/json') else value
  self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)))
  self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
  self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers();self.wfile.write(body)
 def session(self):
  try:
   jar=SimpleCookie(self.headers.get('Cookie',''));token=jar['fv_admin'].value
   return token if SESSIONS.get(token,0)>time.time() else None
  except (KeyError,TypeError):return None
 def do_GET(self):
  path=self.path.split('?')[0]
  if path=='/api/admin/records':
   if not self.session():return self.send(401,{'error':'Войдите с паролем'})
   with connect() as c:rows=c.execute('SELECT code,child,parent,updated FROM families ORDER BY updated DESC').fetchall()
   return self.send(200,{'records':[{'code':r[0],'child':json.loads(r[1]) if r[1] else None,'parent':json.loads(r[2]) if r[2] else None,'updatedAt':r[3]} for r in rows]})
  if path=='/':path='/index.html'
  public=ROOT/'public'; target=(public/path.lstrip('/')).resolve()
  if not target.is_relative_to(public) or not target.is_file():return self.send(404,{'error':'Страница не найдена'})
  import mimetypes
  self.send(200,target.read_bytes(),mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
 def do_POST(self):
  # Same-origin requests only; JSON prevents browser form submissions.
  origin=self.headers.get('Origin')
  if origin and origin not in ['http://'+self.headers.get('Host',''),'https://'+self.headers.get('Host','')]:return self.send(403,{'error':'Запрос отклонён'})
  if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send(415,{'error':'Ожидается JSON'})
  try:
   length=int(self.headers.get('Content-Length','0'))
   if not 0<length<=65536:raise ValueError('Слишком большой запрос')
   data=json.loads(self.rfile.read(length))
   if not isinstance(data,dict):raise ValueError('Неверный запрос')
   path=self.path
   if path=='/api/admin/login':
    ip=self.client_address[0];attempts=[t for t in FAILURES.get(ip,[]) if t>time.time()-900];FAILURES[ip]=attempts
    if len(attempts)>=8:return self.send(429,{'error':'Слишком много попыток. Повторите через 15 минут.'})
    password=data.get('password','')
    if not isinstance(password,str) or len(password)>512:raise ValueError('Неверный пароль')
    salt,digest=setting();actual=hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
    if not hmac.compare_digest(actual,digest):attempts.append(time.time());return self.send(401,{'error':'Неверный пароль'})
    FAILURES.pop(ip,None)
    token=secrets.token_urlsafe(32);SESSIONS[token]=time.time()+28800
    secure='; Secure' if os.environ.get('COOKIE_SECURE')=='1' else ''
    return self.send(200,{'ok':True},cookie=f'fv_admin={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{secure}')
   if path=='/api/admin/logout':
    SESSIONS.pop(self.session(),None)
    return self.send(200,{'ok':True},cookie='fv_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
   if path=='/api/start':
    code='ФВ-'+secrets.token_hex(8).upper()
    with connect() as c:c.execute('INSERT INTO families VALUES (?,NULL,NULL,?)',(code,time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
    return self.send(200,{'code':code})
   code=data.get('code','')
   if not isinstance(code,str) or not re.fullmatch('ФВ-[A-F0-9]{16}',code):raise ValueError('Проверьте семейный код')
   with connect() as c:
    row=c.execute('SELECT child,parent FROM families WHERE code=?',(code,)).fetchone()
    if not row:return self.send(404,{'error':'Семья не найдена. Проверьте код ребёнка.'})
    if path=='/api/family':return self.send(200,{'child':json.loads(row[0]) if row[0] else None})
    if path=='/api/answers':
     child=validate_child(data.get('child'));parent=validate_parent(data.get('parent'))
     if child is not None:c.execute('UPDATE families SET child=? WHERE code=?',(json.dumps(child,ensure_ascii=False),code))
     if parent is not None:c.execute('UPDATE families SET parent=? WHERE code=?',(json.dumps(parent,ensure_ascii=False),code))
     c.execute('UPDATE families SET updated=? WHERE code=?',(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),code))
     return self.send(200,{'ok':True})
   self.send(404,{'error':'Неизвестный запрос'})
  except (ValueError,TypeError,KeyError,AttributeError):self.send(400,{'error':'Проверьте данные анкеты'})
 def log_message(self,fmt,*args):
  # Never log family codes, passwords or answers.
  pass

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--set-password',action='store_true');parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8000);args=parser.parse_args()
 if args.set_password or not setting():set_password()
 if args.set_password:raise SystemExit()
 print(f'Игра: http://{args.host}:{args.port}/\nСтатистика: http://{args.host}:{args.port}/admin.html')
 ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()

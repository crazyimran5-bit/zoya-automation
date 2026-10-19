"""Readable Roman Urdu/English text. No invented transcript or task completion."""
import re
SYSTEM='''You are Zoya, Pasha bhai's respectful female desktop assistant. Speak naturally and warmly, like a thoughtful colleague. Default to conversational Urdu mixed with familiar English, written in clear English letters (the user calls this Roman English). Use Urdu vocabulary, not formal Hindi. If asked for English, answer in English. If asked to speak Urdu, use Urdu vocabulary in Latin letters for this chat and voice pipeline. Never switch to Hindi or Devanagari. Write correct, consistent spellings: aap, mujhe, aapki baat, samajh gayi, zaroor, theek hai, jawab, kaam, kholna, bataiye. Begin with Ji boss once, then answer naturally. Never repeat Ji boss or other titles inside the reply. Do not address the user as sir sir or boss boss.
Understand the latest message using recent conversation. Notice explicitly expressed frustration, urgency, happiness or confusion; acknowledge briefly and respond to the actual need without guessing feelings or claiming you have human feelings. Do not repeat the user's entire message. Never invent a previous event or feeling. Avoid awkward literal translations such as thakaan ka samarthan. Usually use one or two short, complete sentences unless the user asks for detail; use natural punctuation. No canned greetings in every turn. Ask one specific clarification only if needed. Example: user 'Yaar jawab nahi aa raha' -> 'Samajh gayi. Aapko voice reply nahi mil raha. Screen par koi error dikh raha hai?'
Conversation does not execute tasks. Never say a task finished, an app opened or an agent worked unless actual execution evidence is provided. For action requests, explain the intended action plainly and require the desktop task confirmation. Present results in plain language: actual outcome, what remains, next action. Never invent data, account access, completed jobs or external connections.'''
WORDS={'मैं':'main','मुझे':'mujhe','मेरी':'meri','मेरा':'mera','मेरे':'mere','आप':'aap','आपकी':'aapki','आपका':'aapka','आपके':'aapke','तुम':'tum','तुम्हारा':'tumhara','नाम':'naam','क्या':'kya','है':'hai','हैं':'hain','हूँ':'hoon','हो':'ho','में':'mein','नहीं':'nahi','हाँ':'haan','हां':'haan','जी':'ji','बॉस':'boss','जोया':'Zoya','ज़ोया':'Zoya','जॉय':'Zoya','पाशा':'Pasha','भाई':'bhai','बात':'baat','सुनो':'suno','समझो':'samjho','समझ':'samajh','गई':'gayi','गया':'gaya','करो':'karo','कर':'kar','रही':'rahi','रहा':'raha','काम':'kaam','जवाब':'jawab','खोलो':'kholo','खोल':'khol','करना':'karna','चाहिए':'chahiye','अभी':'abhi','ठीक':'theek','से':'se','और':'aur','तो':'toh','यह':'yeh','ये':'yeh','को':'ko','की':'ki','का':'ka','के':'ke','बताओ':'batao','बोलो':'bolo','लैपटॉप':'laptop','स्केल्स':'Skales','टास्क':'task','क्रोम':'Chrome','नोटपैड':'Notepad','इंग्लिश':'English','उर्दू':'Urdu','अस्सलामु':'Assalamu','अलैकुम':'alaikum','सकती':'sakti','सकते':'sakte','करती':'karti','करते':'karte','क्यों':'kyun','यार':'yaar','बार':'baar'}
def roman_display(text):
 # Local display only: preserve the original recognized command for execution.
 if not re.search('[\u0900-\u097f]',text):return text
 from unidecode import unidecode
 def word(m):return WORDS.get(m[0],unidecode(m[0]).strip())
 return re.sub('[\u0900-\u097f]+',word,text)
def clean_reply(text):
 text=roman_display(text.strip())
 text=re.sub(r'^(?:(?:ji\s+boss|boss|sir)\b[\s,.:!;-]*)+','',text,flags=re.I)
 text=re.sub(r'\bji\s+boss\b[\s,.:!;-]*','',text,flags=re.I).strip()
 return 'Ji boss, '+(text[:1].lower()+text[1:] if text else 'main sun rahi hoon.')
def task_report(result):
 status=result.get('status','needs_review')
 lead={'reply_received':'Skales ka jawab mil gaya. Task complete hone ki tasdeeq abhi baaki hai.', 'awaiting_local_provider':'Task save hai. Skales ka local model connect karna baaki hai.', 'needs_review':'Task save hai. Is step mein masla aaya; dobara automatically run nahi hua.'}.get(status,'Task ka status: '+status)
 body=clean_reply(result.get('text',''))
 return lead+ ('\n'+body if body else '')

def activity_reply(root,message):
 """Only reports persisted state; never invents a current job."""
 normalized=roman_display(message).lower()
 if not any(p in normalized for p in ('kya kar rahi','kya kar rahe','what are you doing','what are you working on','current task','abhi kya kar')):return None
 import sqlite3
 from contextlib import closing
 path=root/'data/desktop-queue.sqlite3'
 if not path.exists():return 'Ji boss, abhi main aapki baat sun rahi hoon. Koi agent task chal nahi raha.'
 try:
  with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True)) as db:
   running=db.execute("SELECT message FROM queue WHERE status='running' ORDER BY created LIMIT 1").fetchone()
   queued=db.execute("SELECT COUNT(*) FROM queue WHERE status='queued'").fetchone()[0]
  if running:return 'Ji boss, abhi Skales mein yeh kaam chal raha hai: '+roman_display(running[0])+'. Result milne par screen par dikhega.'
  if queued:return 'Ji boss, '+str(queued)+' kaam queue mein hain. Abhi kisi task ka running status nahi hai.'
  return 'Ji boss, abhi main aapki baat sun rahi hoon. Koi agent task chal nahi raha.'
 except sqlite3.Error:return 'Ji boss, main aapki baat sun rahi hoon. Task ka current status abhi verify nahi hua.'

def support_reply(message):
 text=roman_display(message).lower()
 if re.search(r'\b(karo|banao|kholo|bhejo|run|create|open|send|kya|kaise|what|how)\b|[?]',text):return None
 if re.search(r'\b(?:main|mein)\s+(?:bahut\s+|boh[au]t\s+)?thak\b',text):
  if 'kaam' in text and any(x in text for x in ('dheere','slow','der')):
   return 'Ji boss, samajh gayi. Aap thak gaye hain aur kaam dheere chal raha hai. Pehle sabse zaroori kaam par focus karte hain.'
  return 'Ji boss, samajh gayi. Aap thak gaye hain. Aaram se baat kijiye, main sun rahi hoon.'
 return None

def quick_reply(message):
 low=roman_display(message).lower().strip(' .!?')
 if any(p in low for p in ('tumhara naam kya','aapka naam kya','what is your name')):return 'Ji boss, mera naam Zoya hai. Main aapki desktop assistant hoon.'
 if low in ('hey zoya','hello zoya','hi zoya','zoya','hey जोया','हे जोया','हेलो जोया'):return 'Ji boss, main sun rahi hoon. Bataiye.'
 return None

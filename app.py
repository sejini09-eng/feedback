"""나노입자실험 글쓰기 피드백 — Streamlit 버전

흐름
  1. 학생이 1차 글을 쓰고 제출한다.
  2. 교사·전문가가 피드백(AI 초안, 전문가 1, 전문가 2)을 쓰고, 교사가 학생에게 보낸다.
  3. 학생이 피드백을 읽고 2차 수정본을 제출한다.
  4. 같은 방법으로 최종 피드백을 보낸다.

설정은 .streamlit/secrets.toml (Streamlit Cloud에서는 앱 설정의 Secrets)에 넣는다.
예시는 .streamlit/secrets.toml.example 참고.
"""

from __future__ import annotations

import difflib
import html
import os
import time
from datetime import datetime, timedelta, timezone

import anthropic
import streamlit as st
from sqlalchemy import (
    Column, Float, Integer, MetaData, String, Table, Text, UniqueConstraint,
    create_engine, delete, insert, select, update,
)

st.set_page_config(page_title="나노입자실험_글쓰기_피드백", page_icon="📝", layout="wide")

KST = timezone(timedelta(hours=9))
SLOTS = ["ai", "e1", "e2"]
ROLE_NAMES = {"teacher": "교사", "e1": "전문가 1", "e2": "전문가 2", "student": "학생"}

DEF = {
    "question": "물질의 색은 물질의 특성인가? 실험 결과를 근거로 자신의 주장을 펼치는 글을 쓰시오.",
    "criteria": (
        "주장: 범위나 조건을 붙여 분명하게 썼는가\n"
        "근거의 다양성: 자료를 여러 개 쓰고, 같은 물질인데 색이 다른 경우와 다른 물질인데 색이 같은 경우를 모두 다루었는가\n"
        "추론: 각 결과가 주장을 어떻게 뒷받침하는지 연결했는가\n"
        "과학 개념: 흡수, 산란, 보색을 정확히 썼는가\n"
        "반론: 반론을 제시하고 증거로 재반박했는가"
    ),
    "context": (
        "1. 은거울 반응: 질산은과 암모니아수로 만든 용액에 포도당을 넣자 시험관 벽에 은색 거울이 생김\n"
        "2. 은 나노입자 합성: 질산은에 수소화붕소나트륨을 넣자 투명한 노란 용액이 됨 (시간이 지나 뭉치면 색이 변함)\n"
        "3. 틴들 현상: 나노입자 용액과 같은 색으로 만든 식용 색소 용액에 레이저를 비추자 나노입자 용액에서만 빛줄기가 보임\n"
        "4. 금 나노입자 합성과 UV 측정: 환원제 농도에 따라 붉은색에서 보라색까지 달라지고, 최대 흡수 파장이 약 520nm에서 더 긴 쪽으로 이동함\n"
        "5. 리쿠르고스 컵: 4세기 로마 유리잔. 앞에서 빛을 비추면 초록, 뒤에서 비추면 빨강으로 보임"
    ),
    "e1Name": "전문가 1",
    "e2Name": "전문가 2",
}


# ───────────────────────── 설정 · 저장소 ─────────────────────────
def secret(key: str, default: str = "") -> str:
    try:
        v = st.secrets.get(key, None)
    except Exception:  # secrets.toml이 없을 때
        v = None
    if v is None:
        v = os.environ.get(key, default)
    return str(v)


meta = MetaData()
T_CONFIG = Table("config", meta, Column("key", String(50), primary_key=True), Column("value", Text))
T_STUDENTS = Table(
    "students", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("label", String(40), nullable=False, unique=True),
    Column("pin_hash", String(200), nullable=False),
    Column("created_at", Float),
    Column("t1", Text, default=""), Column("s1", Float), Column("u1", Float),
    Column("t2", Text, default=""), Column("s2", Float), Column("u2", Float),
)
T_DRAFTS = Table(
    "drafts", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("student_id", Integer, nullable=False),
    Column("round", Integer, nullable=False),
    Column("slot", String(4), nullable=False),
    Column("text", Text, default=""),
    Column("updated_at", Float), Column("by", String(20)), Column("src", String(10)),
    UniqueConstraint("student_id", "round", "slot"),
)
T_SENT = Table(
    "sent", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("student_id", Integer, nullable=False),
    Column("round", Integer, nullable=False),
    Column("ai", Text, default=""), Column("e1", Text, default=""), Column("e2", Text, default=""),
    Column("at", Float),
    UniqueConstraint("student_id", "round"),
)


@st.cache_resource
def engine():
    url = secret("DB_URL", "sqlite:///feedback.db")
    if url.startswith("postgres://"):  # 일부 서비스가 주는 옛 형식
        url = "postgresql://" + url[len("postgres://"):]
    eng = create_engine(url, pool_pre_ping=True)
    meta.create_all(eng)
    return eng


def q(stmt):
    with engine().begin() as c:
        return c.execute(stmt)


def rows(stmt) -> list[dict]:
    with engine().begin() as c:
        return [dict(r._mapping) for r in c.execute(stmt)]


def cfg_all() -> dict:
    d = dict(DEF)
    for r in rows(select(T_CONFIG)):
        if (r["value"] or "").strip():
            d[r["key"]] = r["value"]
    return d


def cfg_save(d: dict):
    with engine().begin() as c:
        for k, v in d.items():
            if c.execute(select(T_CONFIG).where(T_CONFIG.c.key == k)).first():
                c.execute(update(T_CONFIG).where(T_CONFIG.c.key == k).values(value=v))
            else:
                c.execute(insert(T_CONFIG).values(key=k, value=v))


def students() -> list[dict]:
    return rows(select(T_STUDENTS).order_by(T_STUDENTS.c.created_at, T_STUDENTS.c.id))


def student(sid: int) -> dict | None:
    r = rows(select(T_STUDENTS).where(T_STUDENTS.c.id == sid))
    return r[0] if r else None


def drafts_for(sid: int, r: int) -> dict:
    out = {sl: {"text": "", "updated_at": None, "by": None, "src": None} for sl in SLOTS}
    for d in rows(select(T_DRAFTS).where(T_DRAFTS.c.student_id == sid, T_DRAFTS.c.round == r)):
        out[d["slot"]] = d
    return out


def draft_save(sid: int, r: int, slot: str, text: str, by: str, src: str):
    vals = dict(text=text, updated_at=time.time(), by=by, src=src)
    with engine().begin() as c:
        cond = (T_DRAFTS.c.student_id == sid) & (T_DRAFTS.c.round == r) & (T_DRAFTS.c.slot == slot)
        if c.execute(select(T_DRAFTS.c.id).where(cond)).first():
            c.execute(update(T_DRAFTS).where(cond).values(**vals))
        else:
            c.execute(insert(T_DRAFTS).values(student_id=sid, round=r, slot=slot, **vals))


def sent_for(sid: int, r: int) -> dict | None:
    s = rows(select(T_SENT).where(T_SENT.c.student_id == sid, T_SENT.c.round == r))
    return s[0] if s else None


def sent_save(sid: int, r: int, fb: dict):
    vals = dict(ai=fb["ai"], e1=fb["e1"], e2=fb["e2"], at=time.time())
    with engine().begin() as c:
        cond = (T_SENT.c.student_id == sid) & (T_SENT.c.round == r)
        if c.execute(select(T_SENT.c.id).where(cond)).first():
            c.execute(update(T_SENT).where(cond).values(**vals))
        else:
            c.execute(insert(T_SENT).values(student_id=sid, round=r, **vals))


def student_delete(sid: int):
    with engine().begin() as c:
        c.execute(delete(T_DRAFTS).where(T_DRAFTS.c.student_id == sid))
        c.execute(delete(T_SENT).where(T_SENT.c.student_id == sid))
        c.execute(delete(T_STUDENTS).where(T_STUDENTS.c.id == sid))


# ───────────────────────── 들어가기 (비밀번호 없음) ─────────────────────────
def login_screen():
    c = cfg_all()
    st.title("나노입자실험_글쓰기_피드백")
    st.caption(c["question"])
    tab_s, tab_t = st.tabs(["학생", "교사 · 전문가"])
    with tab_s:
        st.write("선생님이 알려 준 **번호나 가명**을 적어 주세요. 실명은 쓰지 않아요. 다음에도 같은 이름으로 들어오면 이어서 쓸 수 있어요.")
        with st.form("stu_login"):
            label = st.text_input("번호 또는 가명", max_chars=40, placeholder="예: 3번")
            ok = st.form_submit_button("들어가기", type="primary")
        if ok:
            label = label.strip()
            if not label:
                st.error("번호나 가명을 적어 주세요.")
            else:
                ex = rows(select(T_STUDENTS.c.id).where(T_STUDENTS.c.label == label))
                if not ex:
                    q(insert(T_STUDENTS).values(label=label, pin_hash="", created_at=time.time(), t1="", t2=""))
                    ex = rows(select(T_STUDENTS.c.id).where(T_STUDENTS.c.label == label))
                st.session_state.update(role="student", sid=ex[0]["id"])
                st.rerun()
    with tab_t:
        st.write("누구인지 골라 주세요.")
        cols = st.columns(3)
        for col, who in zip(cols, ["teacher", "e1", "e2"]):
            if col.button("교사" if who == "teacher" else c[who + "Name"], key=f"go_{who}", use_container_width=True):
                st.session_state.update(role=who)
                st.rerun()


# ───────────────────────── 공통 도우미 ─────────────────────────
def when(t: float | None) -> str:
    return datetime.fromtimestamp(t, KST).strftime("%m/%d %H:%M") if t else ""


def pen_name(c: dict, sl: str) -> str:
    return "AI 피드백" if sl == "ai" else c[sl + "Name"]


EMPTY_FB = "<span style='opacity:.6'>(이 피드백은 없어요)</span>"
PEN_COLOR = {"ai": "#1f5bd1", "e1": "#c3263a", "e2": "#a35a00"}


def fb_cards(c: dict, fb: dict | None):
    for sl in SLOTS:
        body = (fb or {}).get(sl) or ""
        st.markdown(
            f"<div style='border:1px solid #e4e2dd;border-left:4px solid {PEN_COLOR[sl]};border-radius:10px;"
            f"padding:10px 14px;margin:8px 0;white-space:pre-wrap'><b style='color:{PEN_COLOR[sl]}'>"
            f"{html.escape(pen_name(c, sl))}</b><br>{html.escape(body) or EMPTY_FB}</div>",
            unsafe_allow_html=True,
        )


def paper(text: str):
    st.markdown(
        f"<div style='border:1px solid #e4e2dd;border-radius:12px;padding:12px 16px;white-space:pre-wrap;"
        f"font-family:\"Noto Serif KR\",serif;line-height:1.9'>{html.escape(text) or '(글 없음)'}</div>",
        unsafe_allow_html=True,
    )


def diff_html(a: str, b: str) -> str:
    A, B = a.split(), b.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, A, B).get_opcodes():
        if op == "equal":
            out.append(html.escape(" ".join(A[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f"<del style='color:#c3263a;opacity:.8'>{html.escape(' '.join(A[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins style='background:#dbe6fb;text-decoration:none'>{html.escape(' '.join(B[j1:j2]))}</ins>")
    return " ".join(out)


# ───────────────────────── 학생 화면 ─────────────────────────
def student_view():
    c = cfg_all()
    me = student(st.session_state["sid"])
    if not me:
        st.session_state.clear()
        st.rerun()
    sid = me["id"]
    s1, s2 = sent_for(sid, 1), sent_for(sid, 2)
    stage = 1 if not me["s1"] else 2 if not s1 else 3 if not me["s2"] else 4 if not s2 else 5

    top = st.columns([4, 1])
    top[0].title(me["label"])
    if top[1].button("나가기"):
        st.session_state.clear()
        st.rerun()
    st.caption(c["question"])
    steps = ["1차 글 쓰기", "피드백 받기", "수정본 쓰기", "최종 피드백"]
    now = [0, 0, 1, 2, 3, 3][stage]
    st.markdown("  ".join(("✅ " if i < now or stage == 5 else "👉 **" if i == now else "⬜ ") + t + ("**" if i == now and stage != 5 else "")
                          for i, t in enumerate(steps)))

    if stage in (1, 3):
        r = 1 if stage == 1 else 2
        if r == 2:
            st.subheader("1차 글에 받은 피드백")
            fb_cards(c, s1)
        st.subheader("1차 글" if r == 1 else "2차 수정본")
        key = f"essay_{sid}_{r}"
        if key not in st.session_state:
            st.session_state[key] = me[f"t{r}"] or (me["t1"] if r == 2 else "")

        def save_draft():
            q(update(T_STUDENTS).where(T_STUDENTS.c.id == sid).values(**{f"t{r}": st.session_state[key], f"u{r}": time.time()}))

        st.text_area("글", key=key, height=360, label_visibility="collapsed", on_change=save_draft,
                     placeholder="과제를 읽고 내 주장과 근거를 써 보세요." if r == 1 else "피드백을 읽고 1차 글을 고쳐 써 보세요.")
        n = len("".join(st.session_state[key].split()))
        st.caption(f"{n}자 (공백 제외) · 다른 곳을 누르면 저장돼요" + (f" · 마지막 저장 {when(me[f'u{r}'])}" if me[f"u{r}"] else ""))
        if r == 2:
            with st.expander("1차 글 다시 보기"):
                paper(me["t1"])
        cols = st.columns([1, 1, 3])
        if cols[0].button("임시 저장"):
            save_draft()
            st.toast("저장했어요.")
        with cols[2]:
            sure = st.checkbox("제출하면 더 고칠 수 없다는 것을 알아요.")
        if cols[1].button("1차 글 제출하기" if r == 1 else "수정본 제출하기", type="primary", disabled=not sure):
            text = st.session_state[key]
            if not text.strip():
                st.error("글을 먼저 써 주세요.")
            else:
                q(update(T_STUDENTS).where(T_STUDENTS.c.id == sid).values(**{f"t{r}": text, f"s{r}": time.time(), f"u{r}": time.time()}))
                st.rerun()
    elif stage == 2:
        st.info("**1차 글을 제출했어요.** AI와 전문가 선생님들이 피드백을 쓰고 있어요. 조금 뒤에 '새로 고침'을 눌러 보세요.")
        st.button("새로 고침")
        st.subheader("내가 제출한 글")
        paper(me["t1"])
    elif stage == 4:
        st.info("**수정본을 제출했어요.** 최종 피드백을 기다려 주세요.")
        st.button("새로 고침")
        st.subheader("내가 제출한 수정본")
        paper(me["t2"])
        with st.expander("1차 글과 받은 피드백"):
            paper(me["t1"])
            fb_cards(c, s1)
    else:
        st.success("**모두 끝났어요.** 수정본에 대한 최종 피드백을 읽어 보세요.")
        st.subheader("수정본에 받은 피드백")
        fb_cards(c, s2)
        st.subheader("내 수정본")
        paper(me["t2"])
        with st.expander("1차 글과 받은 피드백"):
            paper(me["t1"])
            fb_cards(c, s1)


# ───────────────────────── AI 피드백 ─────────────────────────
def build_prompt(c: dict, s: dict, r: int) -> str:
    prev_fb = ""
    if r == 2:
        p1 = sent_for(s["id"], 1) or {}
        prev_fb = "\n\n".join(f"({pen_name(c, sl)})\n{p1[sl]}" for sl in SLOTS if (p1.get(sl) or "").strip())
    r2 = f"""
[이 학생의 1차 글]
{s['t1'] or '(없음)'}

[1차 글에 받은 피드백]
{prev_fb or '(없음)'}
""" if r == 2 else ""
    return f"""너는 중학생의 과학 논증문을 읽고 피드백을 주는 과학 선생님이다.

[과제]
{c['question']}

[학생이 쓸 수 있는 실험 자료]
{c['context']}

[평가 기준]
{c['criteria']}

[과학 개념 점검 포인트]
- 은거울과 은 나노입자는 둘 다 질산은 속 은 이온이 은으로 바뀐 같은 물질이고, 차이는 크기다.
- UV에서 가장 많이 흡수한 빛의 보색이 눈에 보이는 색이다. 약 520nm(초록)를 흡수하면 빨갛게 보인다. 흡수한 색이 그대로 보인다고 쓰면 바로잡아 준다.
- 틴들 현상: 나노입자는 알갱이라서 빛을 산란시켜 빛줄기가 보이고, 색소는 분자로 녹아 있어 빛을 거의 산란시키지 않는다. 흡수와 산란을 섞어 쓰면 바로잡아 준다.
- 리쿠르고스 컵: 앞에서 비추면 나노입자가 산란시킨 초록빛이, 뒤에서 비추면 초록이 흡수되거나 흩어지고 남은 빨간빛이 보인다.
{r2}
[{'수정한 2차 글' if r == 2 else '학생 글'}]
{s[f't{r}']}

[쓰는 방법]
- 선생님이 글 아래에 적어 주는 일반적인 피드백처럼, 제목이나 목록 없이 이어지는 글 한두 문단으로 쓴다.
- 중학생이 읽기 쉽게 '~해요' 체로, 200~300자 안팎으로 짧게 쓴다.
- 좋은 점과 더 나아질 점을 자연스럽게 함께 담되, 고칠 점은 가장 중요한 한두 가지만 골라 어떻게 고치면 좋은지 알려 준다.{chr(10) + '- 1차 글보다 나아진 점을 먼저 짧게 말해 준다.' if r == 2 else ''}
- 과학 개념이 틀린 곳이 있으면 그것을 고칠 점으로 우선 알려 준다.
- 점수나 등급은 매기지 않는다. 학생 글을 대신 고쳐 써 주지 말고 방향만 알려 준다.
- 인사말이나 맺음말은 쓰지 않는다."""


@st.cache_resource
def ai_client():
    key = secret("ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=key) if key else None


def ai_feedback(prompt: str, placeholder=None) -> str:
    """AI 피드백 초안을 만든다. 실패하면 RuntimeError(한국어 메시지)."""
    client = ai_client()
    if client is None:
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았어요.")
    try:
        with client.beta.messages.stream(
            model=secret("CLAUDE_MODEL", "claude-opus-5-5"),
            max_tokens=16000,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # 안전 분류기가 거절하면 서버가 다른 모델로 다시 시도
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            acc = ""
            for t in stream.text_stream:
                acc += t
                if placeholder is not None:
                    placeholder.markdown(acc)
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise RuntimeError("API 키가 올바르지 않아요.")
    except anthropic.RateLimitError:
        raise RuntimeError("요청이 많아요. 잠시 뒤에 다시 해 주세요.")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"AI 서버 오류({e.status_code})가 났어요. 잠시 뒤에 다시 해 주세요.")
    except anthropic.APIConnectionError:
        raise RuntimeError("AI 서버에 연결하지 못했어요.")
    if msg.stop_reason == "refusal":
        raise RuntimeError("AI가 이 글에 대한 피드백을 만들지 않았어요. 직접 써 주세요.")
    out = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not out:
        raise RuntimeError("AI 피드백이 비어 있어요. 다시 해 주세요.")
    return out


# ───────────────────────── 교사 · 전문가 화면 ─────────────────────────
def status(s: dict, r: int) -> str:
    if sent_for(s["id"], r):
        return "보냄"
    if s[f"s{r}"]:
        return "제출함"
    if r == 2 and not sent_for(s["id"], 1):
        return "1차 진행 중"
    return "쓰는 중" if (s[f"t{r}"] or "").strip() else "시작 전"


def sheet_html(c: dict, ss: list[dict], r: int) -> str:
    parts = []
    for s in ss:
        d = drafts_for(s["id"], r)
        boxes = "".join(
            f"<div class='box {sl}'><b>{html.escape(pen_name(c, sl))}</b>\n{html.escape(d[sl]['text'] or '') or '(아직 없음)'}</div>"
            for sl in SLOTS)
        parts.append(f"<section class='pg'><h1>논증문 피드백지 · {r}차</h1><p class='who'>{html.escape(s['label'])}</p>"
                     f"<p class='q'><b>과제</b> {html.escape(c['question'])}</p><h2>내 글</h2>"
                     f"<div class='box'>{html.escape(s[f't{r}'] or '') or '(글 없음)'}</div><h2>세 가지 피드백</h2>{boxes}</section>")
    css = ("body{font:15px/1.7 'Malgun Gothic','Apple SD Gothic Neo',sans-serif;color:#111;margin:24px}"
           ".pg{max-width:760px;margin:0 auto 48px;page-break-after:always}h1{font-size:22px;margin:0}"
           ".who{font-size:18px;font-weight:700;margin:4px 0}.q{color:#444}h2{font-size:16px;margin:22px 0 8px}"
           ".box{border:1px solid #ccc;border-radius:8px;padding:12px 14px;margin:8px 0;white-space:pre-wrap}"
           ".box b{display:block}.ai b{color:#1f5bd1}.e1 b{color:#c3263a}.e2 b{color:#a35a00}")
    return f"<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'><title>피드백지 {r}차</title><style>{css}</style></head><body>{''.join(parts)}</body></html>"


def settings_form(c: dict):
    with st.form("settings"):
        qn = st.text_area("과제 질문", c["question"])
        cr = st.text_area("평가 기준 (AI 피드백에 쓰여요)", c["criteria"], height=160)
        cx = st.text_area("실험 자료 요약 (AI 피드백에 쓰여요)", c["context"], height=160)
        a, b = st.columns(2)
        n1 = a.text_input("전문가 1 이름", c["e1Name"])
        n2 = b.text_input("전문가 2 이름", c["e2Name"])
        if st.form_submit_button("저장", type="primary"):
            cfg_save({"question": qn.strip(), "criteria": cr.strip(), "context": cx.strip(), "e1Name": n1.strip(), "e2Name": n2.strip()})
            st.success("저장했어요.")
            st.rerun()


def staff_view():
    role = st.session_state["role"]
    teacher = role == "teacher"
    c = cfg_all()
    ss = students()

    with st.sidebar:
        st.markdown(f"**{'교사' if teacher else c[role + 'Name']}**로 들어왔어요")
        if st.button("나가기"):
            st.session_state.clear()
            st.rerun()
        r = st.radio("차수", [1, 2], format_func=lambda x: "1차 글" if x == 1 else "2차 수정본", horizontal=True, key="round")
        st.button("새로 고침", use_container_width=True)
        if not ss:
            st.info("아직 들어온 학생이 없어요.")
            sel = None
        else:
            labels = {s["id"]: f"{s['label']} · {status(s, r)}" for s in ss}
            ids = list(labels)
            if st.session_state.get("sel") not in ids:
                st.session_state["sel"] = ids[0]
            sel = st.radio("학생", ids, format_func=labels.get, key="sel")
        st.divider()
        if ss:
            st.download_button(f"{r}차 피드백지 모두 받기", sheet_html(c, ss, r), file_name=f"피드백지_{r}차_전체.html",
                               mime="text/html", use_container_width=True)

    st.title("나노입자실험_글쓰기_피드백")
    st.caption(c["question"])

    if teacher:
        with st.expander("과제 설정"):
            settings_form(c)
        todo = [s for s in ss if s[f"s{r}"] and not drafts_for(s["id"], r)["ai"]["text"].strip()]
        if todo and ai_client() and st.button(f"AI 피드백 모두 만들기 ({len(todo)}명)"):
            bar = st.progress(0.0)
            for i, s in enumerate(todo):
                bar.progress(i / len(todo), text=f"AI 피드백 만드는 중: {s['label']} ({i + 1}/{len(todo)})")
                try:
                    draft_save(s["id"], r, "ai", ai_feedback(build_prompt(c, s, r)), role, "ai")
                except RuntimeError as e:
                    st.error(f"{s['label']}에서 멈췄어요. {e}")
                    break
            else:
                st.success(f"AI 피드백 초안 {len(todo)}개를 만들었어요. 읽어 보고 고친 뒤 학생에게 보내 주세요.")
            bar.empty()

    if not sel:
        st.info("학생들에게 앱 주소와 수업 코드를 알려 주세요. 학생이 들어와 글을 쓰기 시작하면 왼쪽 목록에 나타나요.")
        return
    s = student(sel)
    sid = s["id"]
    submitted = bool(s[f"s{r}"])
    sent = sent_for(sid, r)
    d = drafts_for(sid, r)

    h = st.columns([3, 2])
    h[0].subheader(f"{s['label']} · {r}차 {'글' if r == 1 else '수정본'}")
    h[0].caption(("제출 " + when(s[f"s{r}"])) if submitted else "아직 제출하지 않았어요 (아래는 쓰는 중인 글)")

    st.markdown("**학생 글**")
    if r == 2 and not sent_for(sid, 1):
        st.info("1차 피드백을 보낸 뒤에 학생이 수정본을 써요.")
    else:
        paper(s[f"t{r}"] or "")
        st.caption(f"{len(''.join((s[f't{r}'] or '').split()))}자")
    if r == 2 and (s["t1"] or "").strip() and (s["t2"] or "").strip():
        with st.expander("1차 글과 달라진 부분", expanded=True):
            st.markdown(f"<div style='line-height:1.9'>{diff_html(s['t1'], s['t2'])}</div>", unsafe_allow_html=True)

    cols = st.columns(3)
    for col, sl in zip(cols, SLOTS):
        with col:
            st.markdown(f"<b style='color:{PEN_COLOR[sl]}'>● {html.escape(pen_name(c, sl))}</b>", unsafe_allow_html=True)
            can_edit = teacher or role == sl or (sl == "ai")
            key = f"fb_{sid}_{r}_{sl}"
            # DB 값이 바뀌었으면(다른 사람이 저장했거나 AI가 만든 경우) 입력칸을 새 값으로 맞춘다
            mark = f"{key}_v"
            if st.session_state.get(mark) != d[sl]["updated_at"]:
                st.session_state[key] = d[sl]["text"] or ""
                st.session_state[mark] = d[sl]["updated_at"]
            st.text_area(pen_name(c, sl), key=key, height=300, disabled=not can_edit, label_visibility="collapsed",
                         placeholder="학생이 글을 제출하면 'AI로 만들기'로 초안을 만들 수 있어요." if sl == "ai" else "")
            info = d[sl]
            if info["updated_at"]:
                by = info["by"] or ""
                who = "교사" if by == "teacher" else c.get(by + "Name", "") if by in ("e1", "e2") else ""
                st.caption(" · ".join(x for x in ["AI가 쓴 초안" if info["src"] == "ai" else "마지막 저장", who, when(info["updated_at"])] if x))
            if can_edit:
                b1, b2 = st.columns(2)
                if b1.button("저장", key=f"save_{key}"):
                    draft_save(sid, r, sl, st.session_state[key], role, "edited" if sl == "ai" else "human")
                    st.toast("저장했어요.")
                    st.rerun()
                if sl == "ai" and ai_client():
                    if b2.button("AI로 다시 만들기" if info["text"].strip() else "AI로 만들기", key=f"gen_{key}", disabled=not submitted):
                        ph = st.empty()
                        try:
                            draft_save(sid, r, "ai", ai_feedback(build_prompt(c, s, r), ph), role, "ai")
                            st.rerun()
                        except RuntimeError as e:
                            ph.empty()
                            st.error(str(e))

    st.divider()
    fb_now = {sl: drafts_for(sid, r)[sl]["text"] or "" for sl in SLOTS}
    changed = bool(sent) and any((sent[sl] or "") != fb_now[sl] for sl in SLOTS)
    if sent:
        st.caption(f"학생에게 보냄 {when(sent['at'])}" + (" · 보낸 뒤 고친 내용이 있어요. 다시 보내면 학생 화면도 바뀌어요." if changed else ""))
    elif submitted:
        st.caption("아직 학생에게 보내지 않았어요. 각 칸을 '저장'한 뒤 보내 주세요. (저장하지 않은 입력은 보내지지 않아요)")
    a, b, cc = st.columns([2, 2, 3])
    if teacher:
        label = "고친 피드백 다시 보내기" if changed else ("보냄" if sent else "학생에게 피드백 보내기")
        can_send = submitted and any(v.strip() for v in fb_now.values()) and (not sent or changed)
        with cc:
            sure = st.checkbox("보내면 학생 화면에 바로 보여요.", key=f"sure_{sid}_{r}")
        if a.button(label, type="primary", disabled=not (can_send and sure)):
            sent_save(sid, r, fb_now)
            st.success("학생에게 보냈어요.")
            st.rerun()
        if submitted and not sent and b.button("다시 쓰게 하기"):
            q(update(T_STUDENTS).where(T_STUDENTS.c.id == sid).values(**{f"s{r}": None}))
            st.rerun()
        with st.expander("학생 삭제"):
            st.write(f"‘{s['label']}’ 학생의 1차·2차 글과 모든 피드백이 함께 지워져요. 되돌릴 수 없어요.")
            if st.button("지우기", key=f"del_{sid}"):
                student_delete(sid)
                st.session_state.pop("sel", None)
                st.rerun()
    st.download_button("이 학생 피드백지 받기", sheet_html(c, [s], r),
                       file_name=f"피드백지_{r}차_{s['label']}.html", mime="text/html")


# ───────────────────────── 시작 ─────────────────────────
role = st.session_state.get("role")
if role == "student":
    student_view()
elif role in ("teacher", "e1", "e2"):
    staff_view()
else:
    login_screen()

"""나노입자실험 글쓰기 피드백 — Streamlit 버전

흐름
  1. 학생이 1차 글을 쓰고 '피드백 받기'를 누른다.
  2. AI 피드백이 그 자리에서 바로 나타난다.
  3. 학생이 피드백을 읽고 수정본을 써서 제출한다.

교사는 '교사' 화면에서 학생별 1차 글, AI 피드백, 수정본을 보고 과제를 설정한다.
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
AI_COLOR = "#1f5bd1"

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
# t1/s1: 1차 글과 피드백 받은 시각, t2/s2: 수정본과 제출 시각, u1/u2: 마지막 저장 시각
T_STUDENTS = Table(
    "students", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("label", String(40), nullable=False, unique=True),
    Column("pin_hash", String(200), nullable=False),  # 예전 버전과 호환용 (쓰지 않음)
    Column("created_at", Float),
    Column("t1", Text, default=""), Column("s1", Float), Column("u1", Float),
    Column("t2", Text, default=""), Column("s2", Float), Column("u2", Float),
)
# AI 피드백은 round=1, slot='ai' 한 줄에 저장한다
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
        if r["key"] in d and (r["value"] or "").strip():
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


def set_student(sid: int, **vals):
    q(update(T_STUDENTS).where(T_STUDENTS.c.id == sid).values(**vals))


def ai_fb(sid: int) -> dict | None:
    r = rows(select(T_DRAFTS).where(T_DRAFTS.c.student_id == sid, T_DRAFTS.c.round == 1, T_DRAFTS.c.slot == "ai"))
    return r[0] if r and (r[0]["text"] or "").strip() else None


def ai_fb_save(sid: int, text: str):
    vals = dict(text=text, updated_at=time.time(), by="ai", src="ai")
    with engine().begin() as c:
        cond = (T_DRAFTS.c.student_id == sid) & (T_DRAFTS.c.round == 1) & (T_DRAFTS.c.slot == "ai")
        if c.execute(select(T_DRAFTS.c.id).where(cond)).first():
            c.execute(update(T_DRAFTS).where(cond).values(**vals))
        else:
            c.execute(insert(T_DRAFTS).values(student_id=sid, round=1, slot="ai", **vals))


def student_delete(sid: int):
    with engine().begin() as c:
        c.execute(delete(T_DRAFTS).where(T_DRAFTS.c.student_id == sid))
        c.execute(delete(T_STUDENTS).where(T_STUDENTS.c.id == sid))


# ───────────────────────── AI 피드백 ─────────────────────────
def build_prompt(c: dict, essay: str) -> str:
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

[학생 글]
{essay}

[쓰는 방법]
- 선생님이 글 아래에 적어 주는 일반적인 피드백처럼, 제목이나 목록 없이 이어지는 글 한두 문단으로 쓴다.
- 중학생이 읽기 쉽게 '~해요' 체로, 200~300자 안팎으로 짧게 쓴다.
- 좋은 점과 더 나아질 점을 자연스럽게 함께 담되, 고칠 점은 가장 중요한 한두 가지만 골라 어떻게 고치면 좋은지 알려 준다.
- 과학 개념이 틀린 곳이 있으면 그것을 고칠 점으로 우선 알려 준다.
- 점수나 등급은 매기지 않는다. 학생 글을 대신 고쳐 써 주지 말고 방향만 알려 준다.
- 인사말이나 맺음말은 쓰지 않는다."""


@st.cache_resource
def ai_client():
    key = secret("ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=key) if key else None


def ai_feedback(prompt: str, placeholder=None) -> str:
    """AI 피드백을 만든다. 실패하면 RuntimeError(한국어 메시지)."""
    client = ai_client()
    if client is None:
        raise RuntimeError("AI 피드백이 아직 설정되지 않았어요. 선생님에게 알려 주세요.")
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
        raise RuntimeError("AI 피드백 설정(API 키)에 문제가 있어요. 선생님에게 알려 주세요.")
    except anthropic.RateLimitError:
        raise RuntimeError("지금 요청이 많아요. 잠시 뒤에 다시 눌러 주세요.")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"AI 서버 오류({e.status_code})가 났어요. 잠시 뒤에 다시 눌러 주세요.")
    except anthropic.APIConnectionError:
        raise RuntimeError("AI 서버에 연결하지 못했어요. 잠시 뒤에 다시 눌러 주세요.")
    if msg.stop_reason == "refusal":
        raise RuntimeError("AI가 이 글에 대한 피드백을 만들지 못했어요. 선생님에게 알려 주세요.")
    out = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not out:
        raise RuntimeError("AI 피드백이 비어 있어요. 다시 눌러 주세요.")
    return out


# ───────────────────────── 화면 도우미 ─────────────────────────
def when(t: float | None) -> str:
    return datetime.fromtimestamp(t, KST).strftime("%m/%d %H:%M") if t else ""


def fb_card(text: str):
    st.markdown(
        f"<div style='border:1px solid #e4e2dd;border-left:4px solid {AI_COLOR};border-radius:10px;"
        f"padding:10px 14px;margin:8px 0;white-space:pre-wrap;line-height:1.7'>"
        f"<b style='color:{AI_COLOR}'>AI 피드백</b><br>{html.escape(text)}</div>",
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


def nchars(text: str) -> int:
    return len("".join((text or "").split()))


# ───────────────────────── 들어가기 (비밀번호 없음) ─────────────────────────
def login_screen():
    c = cfg_all()
    st.title("나노입자실험_글쓰기_피드백")
    st.caption(c["question"])
    tab_s, tab_t = st.tabs(["학생", "교사"])
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
        st.write("학생들의 글과 AI 피드백을 보고, 과제를 설정해요.")
        if st.button("교사로 들어가기", key="go_teacher", type="primary"):
            st.session_state.update(role="teacher")
            st.rerun()


# ───────────────────────── 학생 화면 ─────────────────────────
def student_view():
    c = cfg_all()
    me = student(st.session_state["sid"])
    if not me:
        st.session_state.clear()
        st.rerun()
    sid = me["id"]
    fb = ai_fb(sid)
    # 1: 1차 글 쓰기  2: 피드백 보고 수정본 쓰기  3: 끝
    stage = 1 if not fb else 2 if not me["s2"] else 3

    top = st.columns([4, 1])
    top[0].title(me["label"])
    if top[1].button("나가기"):
        st.session_state.clear()
        st.rerun()
    st.caption(c["question"])
    st.markdown("  ".join([
        "✅ 1차 글 쓰기" if stage > 1 else "👉 **1차 글 쓰기**",
        "✅ AI 피드백 받기" if stage > 1 else "⬜ AI 피드백 받기",
        "✅ 수정본 제출" if stage == 3 else ("👉 **수정본 제출**" if stage == 2 else "⬜ 수정본 제출"),
    ]))

    if stage == 1:
        st.subheader("1차 글")
        key = f"essay_{sid}_1"
        if key not in st.session_state:
            st.session_state[key] = me["t1"] or ""

        def save1():
            set_student(sid, t1=st.session_state[key], u1=time.time())

        st.text_area("1차 글", key=key, height=360, label_visibility="collapsed", on_change=save1,
                     placeholder="과제를 읽고 내 주장과 근거를 써 보세요.")
        st.caption(f"{nchars(st.session_state[key])}자 (공백 제외) · 다른 곳을 누르면 저장돼요"
                   + (f" · 마지막 저장 {when(me['u1'])}" if me["u1"] else ""))
        st.caption("'피드백 받기'를 누르면 1차 글은 더 고칠 수 없어요. 대신 피드백을 보고 수정본을 써요.")
        if st.button("피드백 받기", type="primary"):
            text = st.session_state[key]
            if not text.strip():
                st.error("글을 먼저 써 주세요.")
            else:
                set_student(sid, t1=text, s1=time.time(), u1=time.time())
                st.subheader("AI 피드백")
                ph = st.empty()
                with st.spinner("AI가 글을 읽고 있어요…"):
                    try:
                        ai_fb_save(sid, ai_feedback(build_prompt(c, text), ph))
                        st.rerun()
                    except RuntimeError as e:
                        ph.empty()
                        st.error(str(e))
        return

    if stage == 2:
        st.subheader("AI 피드백")
        fb_card(fb["text"])
        with st.expander("내 1차 글 보기"):
            paper(me["t1"])
        st.subheader("수정본")
        key = f"essay_{sid}_2"
        if key not in st.session_state:
            st.session_state[key] = me["t2"] or me["t1"] or ""

        def save2():
            set_student(sid, t2=st.session_state[key], u2=time.time())

        st.text_area("수정본", key=key, height=360, label_visibility="collapsed", on_change=save2,
                     placeholder="피드백을 읽고 1차 글을 고쳐 써 보세요.")
        st.caption(f"{nchars(st.session_state[key])}자 (공백 제외) · 다른 곳을 누르면 저장돼요"
                   + (f" · 마지막 저장 {when(me['u2'])}" if me["u2"] else ""))
        sure = st.checkbox("제출하면 더 고칠 수 없다는 것을 알아요.")
        if st.button("수정본 제출하기", type="primary", disabled=not sure):
            text = st.session_state[key]
            if not text.strip():
                st.error("수정본을 써 주세요.")
            else:
                set_student(sid, t2=text, s2=time.time(), u2=time.time())
                st.rerun()
        return

    st.success("**수정본을 제출했어요. 수고했어요!**")
    st.subheader("내 수정본")
    paper(me["t2"])
    with st.expander("1차 글과 AI 피드백 다시 보기"):
        paper(me["t1"])
        fb_card(fb["text"])


# ───────────────────────── 교사 화면 ─────────────────────────
def status(s: dict) -> str:
    if s["s2"]:
        return "수정본 제출"
    if ai_fb(s["id"]):
        return "피드백 받음 · 수정 중"
    return "1차 글 쓰는 중" if (s["t1"] or "").strip() else "시작 전"


def sheet_html(c: dict, ss: list[dict]) -> str:
    parts = []
    for s in ss:
        fb = ai_fb(s["id"])
        parts.append(
            f"<section class='pg'><h1>논증문 피드백지</h1><p class='who'>{html.escape(s['label'])}</p>"
            f"<p class='q'><b>과제</b> {html.escape(c['question'])}</p>"
            f"<h2>1차 글</h2><div class='box'>{html.escape(s['t1'] or '') or '(글 없음)'}</div>"
            f"<h2>AI 피드백</h2><div class='box ai'>{html.escape(fb['text'] if fb else '') or '(아직 없음)'}</div>"
            f"<h2>수정본</h2><div class='box'>{html.escape(s['t2'] or '') if s['s2'] else '(아직 제출 안 함)'}</div></section>")
    css = ("body{font:15px/1.7 'Malgun Gothic','Apple SD Gothic Neo',sans-serif;color:#111;margin:24px}"
           ".pg{max-width:760px;margin:0 auto 48px;page-break-after:always}h1{font-size:22px;margin:0}"
           ".who{font-size:18px;font-weight:700;margin:4px 0}.q{color:#444}h2{font-size:16px;margin:22px 0 8px}"
           ".box{border:1px solid #ccc;border-radius:8px;padding:12px 14px;margin:8px 0;white-space:pre-wrap}"
           ".ai{border-left:4px solid #1f5bd1}")
    return f"<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'><title>피드백지</title><style>{css}</style></head><body>{''.join(parts)}</body></html>"


def settings_form(c: dict):
    with st.form("settings"):
        qn = st.text_area("과제 질문", c["question"])
        cr = st.text_area("평가 기준 (AI 피드백에 쓰여요)", c["criteria"], height=160)
        cx = st.text_area("실험 자료 요약 (AI 피드백에 쓰여요)", c["context"], height=160)
        if st.form_submit_button("저장", type="primary"):
            cfg_save({"question": qn.strip(), "criteria": cr.strip(), "context": cx.strip()})
            st.success("저장했어요.")
            st.rerun()


def teacher_view():
    c = cfg_all()
    ss = students()

    with st.sidebar:
        st.markdown("**교사**로 들어왔어요")
        if st.button("나가기"):
            st.session_state.clear()
            st.rerun()
        st.button("새로 고침", use_container_width=True)
        if not ss:
            st.info("아직 들어온 학생이 없어요.")
            sel = None
        else:
            labels = {s["id"]: f"{s['label']} · {status(s)}" for s in ss}
            ids = list(labels)
            if st.session_state.get("sel") not in ids:
                st.session_state["sel"] = ids[0]
            sel = st.radio("학생", ids, format_func=labels.get, key="sel")
            st.divider()
            st.download_button("피드백지 모두 받기", sheet_html(c, ss), file_name="피드백지_전체.html",
                               mime="text/html", use_container_width=True)

    st.title("나노입자실험_글쓰기_피드백")
    st.caption(c["question"])
    if not ai_client():
        st.warning("ANTHROPIC_API_KEY가 설정되지 않아 학생이 AI 피드백을 받을 수 없어요. Secrets에 넣어 주세요.")
    with st.expander("과제 설정"):
        settings_form(c)

    if not sel:
        st.info("학생들에게 앱 주소를 알려 주세요. 학생이 들어와 글을 쓰기 시작하면 왼쪽 목록에 나타나요.")
        return
    s = student(sel)
    sid = s["id"]
    fb = ai_fb(sid)

    st.subheader(f"{s['label']} · {status(s)}")
    left, right = st.columns(2)
    with left:
        st.markdown("**1차 글**")
        paper(s["t1"] or "")
        st.caption(f"{nchars(s['t1'])}자" + (f" · 피드백 받음 {when(s['s1'])}" if fb else " · 아직 피드백 받기 전 (쓰는 중인 글)"))
    with right:
        st.markdown("**AI 피드백**")
        if fb:
            fb_card(fb["text"])
            st.caption(when(fb["updated_at"]))
        else:
            st.caption("아직 없어요.")

    st.markdown("**수정본**")
    if not fb:
        st.caption("학생이 피드백을 받은 뒤에 수정본을 써요.")
    else:
        paper(s["t2"] or "")
        st.caption(f"{nchars(s['t2'])}자 · " + (f"제출 {when(s['s2'])}" if s["s2"] else "아직 제출 안 함 (쓰는 중인 글)"))
        if (s["t1"] or "").strip() and (s["t2"] or "").strip():
            with st.expander("1차 글과 달라진 부분", expanded=bool(s["s2"])):
                st.markdown(f"<div style='line-height:1.9'>{diff_html(s['t1'], s['t2'])}</div>", unsafe_allow_html=True)

    st.divider()
    a, b = st.columns(2)
    a.download_button("이 학생 피드백지 받기", sheet_html(c, [s]), file_name=f"피드백지_{s['label']}.html", mime="text/html")
    with b.expander("학생 삭제"):
        st.write(f"‘{s['label']}’ 학생의 글과 피드백이 모두 지워져요. 되돌릴 수 없어요.")
        if st.button("지우기", key=f"del_{sid}"):
            student_delete(sid)
            st.session_state.pop("sel", None)
            st.rerun()


# ───────────────────────── 시작 ─────────────────────────
role = st.session_state.get("role")
if role == "student":
    student_view()
elif role == "teacher":
    teacher_view()
else:
    login_screen()

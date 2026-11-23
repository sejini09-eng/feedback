# 나노입자실험_글쓰기_피드백

학생이 논증문 1차 글을 제출하면 AI와 전문가 2명이 피드백을 쓰고, 교사가 학생에게 보내면 학생이 수정본을 다시 제출하는 도구예요.

두 가지 버전이 있어요.

| 파일 | 어디서 쓰나요 |
|---|---|
| `index.html` | claude.ai 아티팩트. claude.ai 저장소·AI 기능을 쓰므로 claude.ai 안에서만 동작해요. |
| `app.py` | Streamlit 앱. 어디서나 배포할 수 있어요. |

## Streamlit 앱

### 화면
- **학생**: 번호나 가명만 적고 들어와요(비밀번호 없음). 1차 글 쓰기 → 제출 → 피드백 확인 → 수정본 제출 → 최종 피드백 확인.
- **교사**: '교사' 버튼으로 들어와요. 학생 목록과 상태, AI 피드백 만들기(한 명씩/모두), 피드백 보내기, 다시 쓰게 하기, 학생 삭제, 과제 설정, 피드백지 내려받기.
- **전문가 1·2**: 학생 글을 보고 자기 칸의 피드백을 써요. AI 초안도 고칠 수 있어요. 보내기는 교사만 해요.

학생에게는 교사가 '보내기'를 누른 피드백만 보여요.

> 비밀번호가 없어서, 앱 주소를 아는 사람은 누구나 교사·전문가 화면에 들어가거나 다른 학생 이름으로 들어갈 수 있어요. 주소는 수업 참여자에게만 알려 주세요.

### 내 컴퓨터에서 실행
```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 값 채우기
streamlit run app.py
```

### Streamlit Community Cloud에 배포
1. https://share.streamlit.io 에서 이 저장소, 브랜치, `app.py`를 골라 배포해요.
2. 앱 설정 > **Secrets**에 `.streamlit/secrets.toml.example` 내용을 붙여 넣고 값을 채워요.
3. **`DB_URL`을 꼭 넣어 주세요.** 넣지 않으면 SQLite 파일에 저장되는데, Streamlit Cloud는 앱이 다시 시작될 때 파일을 지워서 학생 글이 사라질 수 있어요. Supabase나 Neon의 무료 PostgreSQL 주소를 쓰면 돼요.

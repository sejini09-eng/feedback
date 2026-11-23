# 나노입자실험_글쓰기_피드백

학생이 논증문 1차 글을 쓰고 '피드백 받기'를 누르면 AI 피드백이 바로 나타나고, 학생이 그 피드백을 보고 수정본을 제출하는 도구예요.

| 파일 | 어디서 쓰나요 |
|---|---|
| `app.py` | Streamlit 앱. 어디서나 배포할 수 있어요. |
| `index.html` | 예전 claude.ai 아티팩트 버전(전문가 피드백 포함). claude.ai 안에서만 동작해요. |

## Streamlit 앱

### 화면
- **학생**: 번호나 가명만 적고 들어와요(비밀번호 없음). 1차 글 쓰기 → '피드백 받기' → AI 피드백 확인 → 수정본 제출.
- **교사**: '교사로 들어가기'를 눌러요. 학생별 상태, 1차 글, AI 피드백, 수정본(1차 글과 달라진 부분 표시)을 보고, 과제 설정·피드백지 내려받기·학생 삭제를 해요.

> 비밀번호가 없어서, 앱 주소를 아는 사람은 누구나 교사 화면에 들어가거나 다른 학생 이름으로 들어갈 수 있어요. 주소는 수업 참여자에게만 알려 주세요.

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

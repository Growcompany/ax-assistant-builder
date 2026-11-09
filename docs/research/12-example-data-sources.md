# 가상 은행 예시 자료 — 다운로드 경로·형식·저작권

(2026-11-09 조사 · 조사 에이전트 작성 · 표기: [확인] 원문을 직접 열어 확인 / [검색] 검색 결과 요약만 확인 / [추정] 해석. 결정에 반영한 내용은 `00-summary.md` 6절과 M-1 계획의 '변경 기록' 참고)

## 요약

- **법령 3종(은행법·금소법·전자금융거래법)은 API 키 없이 받을 수 있다.** 국가법령정보센터 화면 JS가 쓰는 내부 엔드포인트에 POST로 요청하면 PDF/HWP/HWPX/DOC가 `Content-Disposition: attachment`로 내려온다. 은행법은 4개 형식을 모두 응답 헤더로 확인했다. 금소법·전자금융거래법은 PDF만 확인했다. 은행법 PDF는 메모리에서 세어 보니 35쪽(234,994B)이다. 본문 HTML은 `lsInfoR.do`에 GET으로 받는다. `curl -I`(HEAD)는 404를 돌려주므로 스크립트는 GET/POST를 써야 한다. [확인] https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=282001
- **전자금융감독규정(금융위원회고시 제2026-29호)**: 전문 HTML은 `admRulInfoR.do`에 GET으로 받을 수 있다(조문 헤더 103개). PDF 엔드포인트는 조문 선택 파라미터 없이 부르면 1쪽만 나온다. 첨부 원문 PDF(`flDownload.do?flSeq=167118077`, 24쪽)도 받을 수 있지만 전문인지 개정문인지는 확인하지 못했다. [확인/미확인]
- **공정위 표준약관 2종**: 공정위 표준약관 게시판에서 키 없이 직접 다운로드된다. **형식은 HWPX만** 있고, 최신판은 '24.9.27. 개정본이다. 응답 첫 바이트가 `PK`로 시작해 HWPX(zip)임을 확인했다. [확인] https://www.ftc.go.kr/www/selectBbsNttList.do?bordCd=201&key=202
- **OpenAPI**
  - 법제처 DRF: `OC`가 필요하다. `OC=test`로 부르면 "사용자 정보 검증에 실패"가 돌아온다. 승인에는 1~2일이 걸린다. [확인]
  - finlife: 개인 키는 신청 즉시 발급되고 하루 10,000건까지 조회된다. 다만 이용약관에 **"정보를 복제 또는 저장하거나 전송할 수 없습니다"** 조항이 있다. [확인]
- **저작권 판단**
  - 법령·고시: 저작권법 제7조 제1·2호에 따라 보호받지 않는다. 법제처도 영리 목적을 포함한 자유 이용을 허용한다(출처 표기 필수). → 공개 저장소에 커밋해도 된다.
  - 표준약관: 회색지대다. → 다운로드 스크립트로 받는 쪽을 권장한다.
  - 은행 상품설명서: 은행의 업무상저작물이다. → 커밋 금지. 링크와 스크립트만 두고, 데모용은 가상 설명서를 직접 쓰는 것을 권장한다.
  - 현행 저작권법(시행 2026.10.29.)에는 TDM(정보분석) 면책 조항이 없다. [확인/추정]
- **평가 데이터셋**
  - `allganize/RAG-Evaluation-Dataset-KO`: MIT, 300문항(도메인당 60), 원문 PDF는 저장소에 없고 출처 URL만 있다. [확인]
  - 보조 후보: `mteb/AutoRAGRetrieval`(MIT), `lbox/kbl`·`lbox/kcl`(CC BY-NC 4.0), `BCCard/BCAI-Finance-Kor-Embedding-Triplet`(CC BY 4.0), KorFinVDR(어노테이션 CC BY 4.0).
  - AI Hub 금융·법률 MRC는 제3자 제공을 금지한다. HF에 올라온 재업로드본은 쓰지 않는 것을 권장한다.

---

## 문서별 소스 표

(작업 방식: 응답 헤더와 첫 바이트, PDF 쪽수만 메모리에서 확인했고 파일은 저장하지 않았다.)

| doc_id 제안 | 제목 (현행 버전) | 유형 | 기본 URL | 형식 | 키 | 라이선스 | 확인 상태 |
|---|---|---|---|---|---|---|---|
| `law_banking_act` | 은행법 [시행 2026.7.1.] [법률 제21296호, 2025.12.30., 일부개정] · MST(lsiSeq)=282001, 법령ID=001549 | 법률 | https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=282001&chrClsCd=010202&efYd=20260701&ancYnChk=0 (HTML) · PDF/HWP/HWPX/DOC는 아래 레시피 | HTML, PDF, HWP, HWPX, DOC | 불필요 | 저작권법 제7조 제1호 비보호 + 법제처 자유이용(출처 표기) | [확인] PDF 35쪽, DOC 398,050B, HWP·HWPX 헤더 |
| `law_fin_consumer_protection` | 금융소비자 보호에 관한 법률 [시행 2026.1.2.] [법률 제21065호, 2025.10.1., 타법개정] · MST=277247, 법령ID=013704 | 법률 | https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=277247&chrClsCd=010202&efYd=20260102&ancYnChk=0 | HTML, PDF (HWP·HWPX·DOC는 같은 경로로 추정) | 불필요 | 위와 같음 | [확인] PDF 헤더(`…(제21065호)(20260102).pdf`) / 나머지 형식 [추정] |
| `law_electronic_fin_transactions` | 전자금융거래법 [시행 2026.10.2.] [법률 제22048호, 2026.9.29., 일부개정] · MST=290247, 법령ID=010199 | 법률 | https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=290247&chrClsCd=010202&efYd=20261002&ancYnChk=0 | HTML, PDF (나머지 [추정]) | 불필요 | 위와 같음 | [확인] PDF 헤더 |
| `reg_efs_supervision` | 전자금융감독규정 [시행 2026.7.15.] [금융위원회고시 제2026-29호, 2026.7.15., 일부개정] · admRulSeq=2100000282622, admRulId=21828 | 행정규칙(고시) | 전문 HTML: https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2100000282622 · 첨부 PDF: https://www.law.go.kr/LSW/flDownload.do?flSeq=167118077 | HTML, PDF(첨부 24쪽), 별표 HWP/PDF | 불필요 | 제7조 제2호 비보호 + 법제처 자유이용 | [확인] HTML 전문(조문 헤더 103개). 첨부 PDF가 전문인지 개정문인지는 [미확인] |
| `terms_ftc_loan_household` | [제10006호] 은행여신거래기본약관(가계용) ('24.9.27. 개정) | 공정위 표준약관 | 게시글: https://www.ftc.go.kr/www/selectBbsNttView.do?key=202&bordCd=201&nttSn=11201 · 파일: https://www.ftc.go.kr/www/downloadBbsFile.do?atchmnflNo=14865 | HWPX (249,584B) | 불필요 | 불명확. 게시물에 공공누리 표시 없음. 아래 판단 참고 | [확인] |
| `terms_ftc_deposit` | [제10012호] 예금거래기본약관 ('24.9.27. 개정) | 공정위 표준약관 | 게시글: https://www.ftc.go.kr/www/selectBbsNttView.do?key=202&bordCd=201&nttSn=11202 · 파일: https://www.ftc.go.kr/www/downloadBbsFile.do?atchmnflNo=14866 | HWPX (214,105B, 첫 바이트 `PK`) | 불필요 | 위와 같음 | [확인] |
| (참고) `terms_ftc_loan_corporate` | [제10005호] 은행여신거래기본약관(기업용) ('24.9.27.) | 표준약관 | nttSn=11200 / atchmnflNo=14864 | HWPX | 불필요 | 위와 같음 | [확인] |
| `prod_deposit_fixed` | **(권장) 가상 은행 정기예금 상품설명서, 직접 작성** / 실물 참고: 하나은행 정기예금 요약 상품설명서 | 상품설명서 | 실물 예: https://image.kebhana.com/cont/download/leaflet/0100263000101.pdf | PDF (285,563B, Last-Modified 2018-02-05) | 불필요 | 은행 저작물(보호). 커밋 금지 | [확인] 200 PDF / 상품명(『3·6·9 정기예금』 등) [검색] |
| `prod_savings_installment` | **(권장) 가상 적금 상품설명서, 직접 작성** / 실물 참고: KB 장병내일준비적금 상품설명서(국방부 게시), 하나 급여하나 월복리 적금 요약설명서 | 상품설명서 | https://mdc.mnd.go.kr/sites/fmc/files/kb-02.pdf · https://mbp.hanabank.com/cont/pdf/product/0100272000101_d.pdf | PDF (414,123B / 562,693B, 5쪽) | 불필요 | 위와 같음 | [확인] 200 PDF / 상품명·유효기간(2026.01.01~2027.11.30) [검색] |
| `prod_loan_mortgage` | **(권장) 가상 주택담보대출 상품설명서, 직접 작성** / 실물 참고: 하나은행 상품설명서 양식(2023) | 상품설명서 | https://image.kebhana.com/cont/download/documents/manual/0000005060902_20230116_m.pdf | PDF (741,839B) | 불필요 | 위와 같음 | [확인] 200 PDF / 어떤 상품인지 [미확인] |
| `policy_consumer_protection_ic` | (가상) 금융소비자보호 내부통제기준 | 가상 내규 | 저장소 `data/synthetic/` | MD → PDF/HWPX로도 생성해 파서 데모 | - | 자체 작성(CC BY 4.0 또는 MIT 권장) | 금소법상 내부통제기준 마련 의무를 근거로 둠 [추정] |
| `policy_efs_security` | (가상) 전자금융 보안·이상거래탐지(FDS) 업무지침 | 가상 내규 | 위와 같음 | 위와 같음 | - | 위와 같음 | 전자금융감독규정과 대응시킴 [추정] |
| `policy_credit_review` | (가상) 가계여신 심사·상담 업무매뉴얼 | 가상 내규 | 위와 같음 | 위와 같음 | - | 위와 같음 | 여신약관·금소법 설명의무와 대응시킴 [추정] |

### 다운로드 레시피 (키 불필요, 비공식 내부 엔드포인트)

화면 JS(`/LSW/js/ls/lsOutPutJoNew.js`, `/LSW/js/admrul/admrulOutPutJoNew.js`)에서 역추적한 경로다. 사이트 개편 시 바뀔 수 있으므로 공식 DRF API를 1순위로, 아래 경로를 대체 수단으로 쓰는 것을 권장한다. [확인] https://www.law.go.kr/LSW/js/ls/lsOutPutJoNew.js · robots.txt는 `Allow: /`이다 [확인] https://www.law.go.kr/robots.txt

```bash
# 0) 법령명 → 현행 lsiSeq/efYd 해석 (GET 200, iframe src에 들어 있음. HEAD는 404) [확인]
curl -s "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%9D%80%ED%96%89%EB%B2%95"   # /법령/은행법
#   → src="/LSW//lsInfoP.do?lsiSeq=282001&chrClsCd=010202&urlMode=lsInfoP&efYd=20260701&ancYnChk=0"
#   행정규칙: /행정규칙/전자금융감독규정 → admRulInfoP.do?admRulSeq=2100000282622

# 1) 본문 HTML (GET) [확인]
curl -s "https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=282001&chrClsCd=010202&efYd=20260701&ancYnChk=0"
curl -s "https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2100000282622"

# 2) 법령 PDF/HWP/HWPX/DOC (POST, 같은 form body) [확인: 은행법 4형식, 금소법·전자금융거래법 PDF]
BODY="lsiSeq=282001&chrClsCd=010202&joAllCheck=Y&efYd=20260701&coverDpYn=1&spaceCls=1"
curl -X POST --data "$BODY" "https://www.law.go.kr/LSW/lsPdfPrint.do?efGubun="                                       # application/pdf
curl -X POST --data "$BODY" "https://www.law.go.kr/LSW/lsNewHwpSave.do?trSeq=282001&efDvPop=&nwJoYnInfo=&efGubun="    # application/hwp
curl -X POST --data "$BODY" "https://www.law.go.kr/LSW/lsHwpxSave.do?trSeq=282001&efDvPop=&nwJoYnInfo=&efGubun="      # application/hwp+zip
curl -X POST --data "$BODY" "https://www.law.go.kr/LSW/lsRtfPrint.do?efGubun="                                        # application/msword

# 3) 전자금융감독규정 첨부 원문 PDF (GET) [확인: 24쪽, 전문 여부는 미확인]
curl "https://www.law.go.kr/LSW/flDownload.do?flSeq=167118077"
#   ※ admRulPdfPrint.do 등은 joNo(조문목록) 없이 부르면 1쪽만 나옴 [확인] → HTML 또는 DRF 사용 권장

# 4) 공정위 표준약관 HWPX (GET) [확인]
curl "https://www.ftc.go.kr/www/downloadBbsFile.do?atchmnflNo=14865"   # 여신(가계용)
curl "https://www.ftc.go.kr/www/downloadBbsFile.do?atchmnflNo=14866"   # 예금거래기본약관
```

- 법령을 개정하면 lsiSeq가 바뀐다. 그래서 0)단계로 현행 lsiSeq를 다시 해석한 뒤 `manifest.yaml`에 `doc_id, source_url, method, params, format, sha256, retrieved_at, 시행일, 공포번호`를 기록하는 방식을 권장한다. [추정]
- HWPX는 ZIP 안에 OWPML XML이 든 구조다. `zipfile`로 열어 본문 XML을 파싱하면 HWP 전용 도구 없이 처리할 수 있다. 공정위 파일의 첫 바이트 `PK`는 [확인]했고, 파싱 방법은 [추정]이다.
- 표준약관 게시판에는 2016-10-21 구판도 남아 있다(제10006호 nttSn=11156, 제10005호 nttSn=11155). "개정 전후 비교 질의" 데모 자료로 쓸 수 있다. [확인] https://www.ftc.go.kr/www/selectBbsNttList.do?bordCd=201&key=202

---

## OpenAPI 정리

### 1) 국가법령정보 공동활용 (open.law.go.kr, DRF)

| 항목 | 내용 | 상태 |
|---|---|---|
| 신청 절차 | 회원가입(로그인 ID는 이메일) → 활용할 데이터 선택 후 활용신청(`/LSO/openApi/cuAskList.do`) → 담당자 승인 뒤 사용. "승인 처리는 신청후 1~2일 이내" | [확인] https://open.law.go.kr/LSO/information/guide.do |
| OC 값 | 가이드 정의는 "신청한 API인증값". 실무상 가입 이메일의 `@` 앞부분이라는 안내가 있다(법제처 계열 다른 서비스 기준). 키 관리 메뉴는 `/LSO/usr/usrOcInfoMod.do` | [확인] https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lsNwInfoGuide / [검색] https://opinion.lawmaking.go.kr/api/operationGuideInfo |
| IP/도메인 | `OC=test`로 부르면 `"사용자 정보 검증에 실패하였습니다." / "…정확한 서버장비의 IP주소 및 도메인주소를 등록해 주세요."` → 신청할 때 호출 서버의 IP와 도메인을 등록해야 한다 | [확인] `curl "https://www.law.go.kr/DRF/lawSearch.do?OC=test&target=law&type=JSON&query=은행법"` |
| 목록 조회 | `https://www.law.go.kr/DRF/lawSearch.do?OC={OC}&target=law&type=JSON&query=은행법` (시행일 기준 목록은 `target=eflaw`) | [확인] https://open.law.go.kr/LSO/openApi/openApiManual.do |
| 법령 본문 | `http://www.law.go.kr/DRF/lawService.do?OC={OC}&target=law&MST=282001&type=JSON` 또는 `&ID=001549`. 선택 파라미터: `JO`(조번호, 생략 시 전 조문. 6자리 = 조 4자리 + 가지 2자리, 예: 제8조는 `000800`), `LANG=KO/ORI`, `LM`·`LD`·`LN` | [확인] 가이드 / JO 6자리 형식은 [검색] |
| 본문 응답 필드 | 법령ID, 공포일자, 공포번호, 법령명_한글, 시행일자, 제개정구분, 소관부처명 / 조문번호, 조문가지번호, 조문제목, 조문시행일자, 조문내용 / 항번호·항내용·호번호·호내용·목번호·목내용 / 부칙공포일자·부칙내용 / 별표번호·별표제목·별표내용·별표서식 파일링크 / 개정문내용, 제개정이유내용 | [확인] https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lsNwInfoGuide |
| 행정규칙 본문 | `http://www.law.go.kr/DRF/lawService.do?OC={OC}&target=admrul&ID=2100000282622&type=XML` (ID는 행정규칙 일련번호, LID는 행정규칙ID=21828, LM은 규칙명). 응답: 행정규칙명, 발령일자, 발령번호, 시행일자, 현행여부, 소관부처명, 조문내용, 부칙, 별표서식파일링크, 별표서식PDF파일링크, 첨부파일링크 | [확인] https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=admrulInfoGuide |
| 조항호목 단위 | `target=lawjosub` 별도 API | [검색] https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lsNwJoListGuide |
| 별표 파일 | 응답의 별표 링크 값을 `https://www.law.go.kr` 뒤에 붙이면 받을 수 있다. 예: `/LSW/flDownload.do?flSeq=162492787` | [확인] openApiManual.do |
| 이용 조건 | "제공된 법령정보는 영리 목적의 이용을 포함하여 자유로운 활용이 보장" + 출처 미표기 시 저작권법 제137·138조 형사처벌 가능 경고 | [확인] https://open.law.go.kr/LSO/information/guide.do |

**권장 사용법**: 조문 단위로 청킹하고 정답 조문 메타데이터(`doc_id#제8조`)를 만들 때는 DRF JSON을 쓰고, "실제 PDF/HWPX를 넣는 파서 데모"용으로는 웹 POST 경로를 쓰는 이중 구성이 좋다. [추정]

### 2) 금감원 금융상품통합비교공시 (finlife) OpenAPI

| 항목 | 내용 | 상태 |
|---|---|---|
| 키 발급 | 약관·개인정보 동의 → 이용형태(개인/단체) 선택 → 본인인증 → 이메일 인증 → 신청. **개인은 신청 즉시 발급**. 단체는 사업자등록증·요청IP·사용URL이 필요 | [확인] https://finlife.fss.or.kr/finlife/api/finlifeApiKey/list.do?menuNo=700034 |
| 한도 | 개인 1일 10,000건, 페이지당 100건(연금저축은 10건). 단체는 건수 제한 대신 IP 제한. 1인 1키, 12개월 미사용 시 삭제 | [확인] 위 페이지 |
| 공통 요청 | `https://finlife.fss.or.kr/finlifeapi/{서비스}.{json\|xml}?auth={32자리키}&topFinGrpNo=020000&pageNo=1[&financeCd=…]` · topFinGrpNo: 020000 은행 / 030200 여신전문 / 030300 저축은행 / 050000 보험 / 060000 금융투자 | [확인] https://finlife.fss.or.kr/finlife/api/fdrmDpstApi/list.do?menuNo=700052 |
| 엔드포인트 | `depositProductsSearch`(정기예금), `savingProductsSearch`(적금), `mortgageLoanProductsSearch`(주담대), `rentHouseLoanProductsSearch`(전세대출), `creditLoanProductsSearch`(신용대출), `companySearch`(금융회사). 6개 모두 잘못된 키에 `{"result":{"err_cd":"010","err_msg":"미등록 인증키"}}`를 반환하는 것으로 존재를 확인 | [확인] curl |
| 응답 구조 | 최상위 `err_cd, err_msg, total_count, max_page_no, now_page_no`. JSON에서는 `result.baseList`(상품)와 `result.optionList`(옵션)로 나뉘고 `fin_co_no + fin_prdt_cd`로 조인 | [확인] https://finlife.fss.or.kr/finlife/api/houseMrtgApi/list.do?menuNo=700055 |
| 예금 필드 | base: `dcls_month, fin_co_no, kor_co_nm, fin_prdt_cd, fin_prdt_nm, join_way, mtrt_int, spcl_cnd, join_deny, join_member, etc_note, max_limit, dcls_strt_day, dcls_end_day, fin_co_subm_day` / option: `intr_rate_type, intr_rate_type_nm, save_trm, intr_rate, intr_rate2` | [확인] fdrmDpstApi 페이지 |
| 적금 필드 | 예금 필드 + option `rsrv_type, rsrv_type_nm` (S=정액적립식, F=자유적립식) | [확인] https://finlife.fss.or.kr/finlife/api/fdrmEntyApi/list.do?menuNo=700053 |
| 주담대 필드 | base: `loan_inci_expn, erly_rpay_fee, dly_rate, loan_lmt` 등 / option: `mrtg_type(_nm), rpay_type(_nm), lend_rate_type(_nm), lend_rate_min, lend_rate_max, lend_rate_avg` | [확인] houseMrtgApi 페이지 |
| 신용대출 필드 | base: `crdt_prdt_type(_nm), cb_name` / option: `crdt_lend_rate_type(_nm), crdt_grad_1, _4, _5, _6, _10, _11, _12, _13, crdt_grad_avg` | [확인] https://finlife.fss.or.kr/finlife/api/indvlCrdtApi/list.do?menuNo=700057 |
| **이용약관 주의** | 화면에 "금융상품통합비교공시시스템(금융감독원) 금융회사 상품정보" 출처를 표시해야 한다. 실시간 연계는 허용되지만 **"정보를 복제 또는 저장하거나 전송할 수 없습니다"** → 응답 JSON을 저장소에 커밋하거나 벡터DB에 영구 적재하는 것은 약관 위반 소지가 있다 | [확인] 키 신청 페이지 / 해석은 [추정] |

**권장 사용법**: finlife는 "실시간 금리 조회 도구(tool)"로만 에이전트에 붙인다. LangGraph 단계에서 쓰기 좋은 데모다. 가상 상품설명서의 금리와 조건은 실제 분포를 참고하되 가상의 수치로 직접 작성한다. [추정]

---

## 저작권 판단

**근거 조문 (현행 저작권법 [시행 2026.10.29.] [법률 제21595호])** [확인] https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=285693&chrClsCd=010202&efYd=20261029&ancYnChk=0
- 제7조(보호받지 못하는 저작물): 1. 헌법·법률·조약·명령·조례 및 규칙, 2. 국가 또는 지방자치단체의 고시·공고·훈령 그 밖에 이와 유사한 것, 3. 판결·결정 등, 4. 국가 또는 지자체가 작성한 제1~3호의 편집물 또는 번역물, 5. 시사보도.
- 제24조의2(공공저작물의 자유이용): 국가나 지자체가 업무상 작성해 공표한 저작물이나 저작재산권 전부를 보유한 저작물은 허락 없이 이용할 수 있다(예외 4가지).
- 제35조의5(공정한 이용): 4요소로 판단한다. 현행 본문에서 "정보분석" 같은 TDM 면책 조항은 찾지 못했다(검색 기준). [확인/추정]

| 자료 | 판단 | 권고 |
|---|---|---|
| 법령 3종 | 제7조 제1호로 비보호. 법제처는 "영리 목적의 이용을 포함하여 자유로운 활용이 보장"한다고 밝히며 출처 표기를 요구한다. law.go.kr이 만든 PDF/HWP 편집본도 제4호(국가 작성 편집물) 범위로 볼 수 있다 [확인 https://open.law.go.kr/LSO/information/guide.do / 제4호 적용은 추정] | **커밋 가능.** 다만 최신성 때문에 스크립트로 받는 쪽을 기본으로 하고, 정규화한 텍스트·JSON과 `출처: 국가법령정보센터(법제처), 시행일, 공포번호`를 함께 커밋한다 |
| 전자금융감독규정 | 금융위원회 고시이므로 제7조 제2호로 비보호 [확인(조문)/적용은 추정] | 법령과 같이 처리 |
| 공정위 표준약관 | 약관규제법 제19조의3상 표준약관은 사업자·사업자단체가 마련해 심사청구하거나 공정위가 직접 제정하고, 공정위가 "공시"하고 사용을 권장한다 [확인 https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=260021&chrClsCd=010202&efYd=20240807&ancYnChk=0]. 이것이 제7조 제2호의 "고시…그 밖에 이와 유사한 것"에 해당한다는 명시적 해석은 찾지 못했다 [추정]. 공정위 저작권정책은 공정위가 권리 전부를 가진 저작물은 제24조의2로 자유이용할 수 있다고 하면서도, **공공누리가 부착되지 않은 자료는 사전 협의**를 요구한다. 해당 게시물에서는 공공누리 표시를 찾지 못했다 [확인 https://www.ftc.go.kr/www/contents.do?key=520]. 은행 표준약관 초안의 작성 주체(은행연합회 등)는 확인하지 못했다 [미확인] | **원문 커밋은 피하고 다운로드 스크립트(URL + SHA256)를 쓴다.** README에 출처와 개정일을 적는다. 커밋하려면 공정위 담당과(소비자거래정책과)에 확인하는 것이 안전하다. 실제 위험은 낮지만 포트폴리오에서는 보수적으로 가는 것을 권장한다 [추정] |
| 은행 상품설명서 | 법인 명의로 공표된 업무상저작물(제9조)이므로 보호 대상이다. 제7조·제24조의2는 해당하지 않는다. 금리 같은 사실은 보호되지 않지만 문서 표현은 보호된다. 국방부 사이트에 게시돼 있어도 권리가 바뀌지 않는다 [추정]. 참고로 KB경영연구소는 "비영리 목적만, 사전 협의"를 요구한다 [검색 https://kbfg.com/kbresearch/about/policy.do] | **커밋 금지.** ① 데모 기본값은 가상 은행 설명서 3종(직접 작성, 실존 은행명·로고 미사용). ② 실물 테스트는 `scripts/fetch_real_samples.py`로 로컬에만 받고 `.gitignore`에 `data/raw/` 추가. 평가 결과에는 문서 일부 인용만 남긴다 |
| finlife 응답 데이터 | 약관상 복제·저장·전송 금지 [확인] | 커밋 금지. 실시간 조회 도구로만 사용 |
| 가상 내규·가상 설명서 | 자체 저작물 | 커밋한다(CC BY 4.0 권장). 문서마다 "가상 문서, 실존 기관과 무관" 문구를 넣는다 |

선례: allganize와 KorFinVDR도 **원문은 배포하지 않고 URL과 메타데이터만** 공개한다. KorFinVDR은 "한국은행 저작권 정책상 페이지 이미지 재배포 불가"를 명시한다. 같은 패턴을 쓰면 면접에서 설명하기도 좋다. [확인 https://huggingface.co/datasets/MarkrAI/korfinvdr-financial_report]

---

## 평가 데이터셋

### allganize/RAG-Evaluation-Dataset-KO (주 벤치마크)
- 경로: https://huggingface.co/datasets/allganize/RAG-Evaluation-Dataset-KO · 라이선스 **MIT** · lastModified 2024-11-22 · gated 아님 [확인 https://huggingface.co/api/datasets/allganize/RAG-Evaluation-Dataset-KO]
- 파일: `README.md`(11.4kB), `documents.csv`(12.6kB), `rag_evaluation_result.csv`(5.69MB). 뷰어는 default/test 300행이다. [확인]
- `rag_evaluation_result.csv`: 300행 × 52열. 기본 열은 `domain, question, target_answer, target_file_name, target_page_no, context_type`이고, 23개 시스템마다 `*_answer`·`*_ox` 열이 있다. 도메인별 60문항 × 5 도메인(finance, public, medical, law, commerce). `context_type` 분포는 paragraph 148 / image 57 / table 50 / text 45. [확인]
- 원문 PDF 제공 방식: 저장소에는 PDF가 없다. `documents.csv`(열: `domain, file_name, pages, url`, 64행. medical 20, public 12, law 12, finance 10, commerce 10)의 출처 페이지 URL에서 직접 받아야 한다. finance 출처는 한국은행·금융투자협회·금융위 보도자료·한국금융연구원이다. 일부 URL은 게시판 목록 페이지라 링크가 깨질 수 있다. README 표에는 commerce가 9문서로 적혀 있지만 CSV에는 10행이 있다. [확인] MIT는 QA에 적용되고 원문 PDF의 저작권은 각 기관에 있다. [추정]
- finance 문항 예: "시중은행, 지방은행, 인터넷은행의 인가 요건 및 절차…"(정답이 은행법 제8조를 언급)라서 이 프로젝트와 관련도가 높다. [확인]
- 2024년 리더보드 최상위는 Alli(claude3.5-sonnet) 0.847(254/300)이다. 자동평가는 LLM 4개 투표 방식이고 사람 채점 대비 오차는 약 8%다(2024.08 기준). [확인 README]
- 활용: finance 60문항과 PDF 10개로 "외부 일반화" 점수를 내고, 가상 은행 워크스페이스용 골든셋은 별도로 직접 만든다. [추정]

### 그 외 후보

| 데이터셋 | 내용 | 라이선스 | 용도 | 상태 |
|---|---|---|---|---|
| `mteb/AutoRAGRetrieval` | allganize 문서 기반 검색 벤치마크. corpus 720 / queries 114 / qrels 114, 5개 도메인 | MIT | 임베딩·BM25·RRF 검색 단계 평가(MTEB 호환) | [확인] https://huggingface.co/datasets/mteb/AutoRAGRetrieval |
| `lbox/kbl` (KBL, EMNLP 2024 Findings) | 법률지식 7과제(510) + 법률추론 4과제(288) + 변호사시험 4분야(53). 법령·판례 RAG 코퍼스 공개 | CC BY-NC 4.0 (lastModified 2025-05-19) | 법률 open-book RAG 평가. 비상업 포트폴리오에서는 사용 가능, 재배포 시 NC 유지 | [확인 API] / 과제 수치는 [검색] https://arxiv.org/abs/2410.08731 |
| `lbox/kcl` (KCL, 2025~2026) | 변호사시험 기반. `kcl_mcqa`·`kcl_essay`, 문항별 근거 판례 제공 | CC BY-NC 4.0 (2026-01-12). 원자료는 KOGL 1유형 | 근거 기반 법률추론 평가 | [확인 API] / 설명은 [검색] https://huggingface.co/datasets/lbox/kcl |
| `BCCard/BCAI-Finance-Kor-Embedding-Triplet` (+ `…-Embedding-Pair` 코퍼스) | 금융 triplet 45,394건. test 1,000건 골든셋, 등급형 relevance(1~3) | CC BY 4.0 (2026-06-12) | 금융 도메인 임베딩(bge-m3) 비교와 파인튜닝 | [확인] https://huggingface.co/datasets/BCCard/BCAI-Finance-Kor-Embedding-Triplet |
| KorFinVDR (`MarkrAI/korfinvdr-*`, 2026) | 금융 시각문서 검색. 5개 서브셋, 약 18,393쪽, 약 2,423 쿼리. 원문 페이지 이미지는 미배포 | 어노테이션 CC BY 4.0, GitHub MIT | 표·차트가 많은 PDF 검색 평가(확장용) | [확인] https://github.com/Marker-Inc-Korea/kor-fin-vdr |
| (주의) AI Hub "금융, 법률 문서 기계독해 데이터" (dataSetSn=71610) | 2022년 구축, 라벨 401,108건, 원천은 한국은행·금융위·금감원·법제처 등 | 내국인만·로그인·승인 필요. AI Hub 정책상 승인 없는 제3자 제공 금지, "학습용으로만" | 로컬 학습·실험만. 저장소 커밋 불가. HF의 `shchoice/finance-legal-mrc`는 같은 명칭과 5개 서브셋을 자체 표기한 CC-BY-SA로 올린 재업로드로 보여 사용 비권장 [추정] | [확인] https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=71610 · https://www.aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105 |

참고: `BCCard/BCCard-Finance-Kor-QnA`(apache-2.0, jsonl 17.1MB)와 `allganize/financial-mmlu-ko`(455문항, 라이선스 표기 없음)도 있다. 앞의 것은 생성형 QA 학습용, 뒤의 것은 객관식이라 RAG 평가에는 덜 적합하다. [확인 HF API]

---

## 출처

- 국가법령정보센터
  - 은행법: https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=282001
  - 본문 HTML: https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=282001&chrClsCd=010202&efYd=20260701&ancYnChk=0
  - 금소법: https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=277247&chrClsCd=010202&efYd=20260102&ancYnChk=0
  - 전자금융거래법: https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=290247&chrClsCd=010202&efYd=20261002&ancYnChk=0
  - 전자금융감독규정: https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2100000282622
  - 전자금융감독규정 첨부 PDF: https://www.law.go.kr/LSW/flDownload.do?flSeq=167118077
  - 저장 JS: https://www.law.go.kr/LSW/js/ls/lsOutPutJoNew.js · https://www.law.go.kr/LSW/js/admrul/admrulOutPutJoNew.js
  - robots.txt: https://www.law.go.kr/robots.txt
- 저작권법: https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=285693&chrClsCd=010202&efYd=20261029&ancYnChk=0 · 약관규제법: https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=260021&chrClsCd=010202&efYd=20240807&ancYnChk=0
- open.law.go.kr
  - 이용안내: https://open.law.go.kr/LSO/information/guide.do
  - 활용방법: https://open.law.go.kr/LSO/openApi/openApiManual.do
  - 활용가이드 목록: https://open.law.go.kr/LSO/openApi/guideList.do
  - 법령 본문 가이드: https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lsNwInfoGuide
  - 행정규칙 본문 가이드: https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=admrulInfoGuide
  - 조항호목 가이드: https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lsNwJoListGuide
  - OPEN API 신청: https://open.law.go.kr/LSO/openApi/cuAskList.do
  - OC 안내(타 서비스): https://opinion.lawmaking.go.kr/api/operationGuideInfo
- 공정거래위원회
  - 표준약관 게시판: https://www.ftc.go.kr/www/selectBbsNttList.do?bordCd=201&key=202
  - 게시글: https://www.ftc.go.kr/www/selectBbsNttView.do?key=202&bordCd=201&nttSn=11201 · https://www.ftc.go.kr/www/selectBbsNttView.do?key=202&bordCd=201&nttSn=11202
  - 저작권정책: https://www.ftc.go.kr/www/contents.do?key=520
  - 2024.9.30 개정 보도자료(KDI 게재): https://eiec.kdi.re.kr/policy/materialView.do?num=257864
- finlife
  - 오픈API 소개: https://finlife.fss.or.kr/finlife/main/contents.do?menuNo=700029
  - 인증키: https://finlife.fss.or.kr/finlife/api/finlifeApiKey/list.do?menuNo=700034
  - 정기예금: https://finlife.fss.or.kr/finlife/api/fdrmDpstApi/list.do?menuNo=700052
  - 적금: https://finlife.fss.or.kr/finlife/api/fdrmEntyApi/list.do?menuNo=700053
  - 주담대: https://finlife.fss.or.kr/finlife/api/houseMrtgApi/list.do?menuNo=700055
  - 신용대출: https://finlife.fss.or.kr/finlife/api/indvlCrdtApi/list.do?menuNo=700057
- 상품설명서·은행
  - 하나은행 상품공시실: https://www.kebhana.com/cont/mall/mall09/mall0901/index.jsp
  - 하나은행 PDF: https://image.kebhana.com/cont/download/leaflet/0100263000101.pdf · https://mbp.hanabank.com/cont/pdf/product/0100272000101_d.pdf · https://image.kebhana.com/cont/download/documents/manual/0000005060902_20230116_m.pdf
  - 국방부 게시 PDF: https://mdc.mnd.go.kr/sites/fmc/files/kb-02.pdf · https://mdc.mnd.go.kr/sites/fmc/files/shinhan-02.pdf · https://mdc.mnd.go.kr/sites/fmc/files/ibk-02.pdf
  - 은행연합회 소비자포털: https://portal.kfb.or.kr/compare/receiving_deposit_3.php
  - KB경영연구소 저작권 정책: https://kbfg.com/kbresearch/about/policy.do
- 데이터셋
  - allganize: https://huggingface.co/datasets/allganize/RAG-Evaluation-Dataset-KO · https://huggingface.co/datasets/allganize/RAG-Evaluation-Dataset-KO/tree/main
  - AutoRAGRetrieval: https://huggingface.co/datasets/mteb/AutoRAGRetrieval
  - KBL: https://huggingface.co/datasets/lbox/kbl · https://arxiv.org/abs/2410.08731
  - KCL: https://huggingface.co/datasets/lbox/kcl
  - BCCard: https://huggingface.co/datasets/BCCard/BCAI-Finance-Kor-Embedding-Triplet · https://huggingface.co/datasets/BCCard/BCCard-Finance-Kor-QnA
  - KorFinVDR: https://huggingface.co/datasets/MarkrAI/korfinvdr-financial_report · https://github.com/Marker-Inc-Korea/kor-fin-vdr
  - AI Hub 금융·법률 MRC: https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=71610 · https://www.aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105
  - HF 재업로드본: https://huggingface.co/datasets/shchoice/finance-legal-mrc
  - financial-mmlu-ko: https://huggingface.co/datasets/allganize/financial-mmlu-ko

(프로젝트 파일은 만들거나 수정하지 않았다. 확인 과정에서 받은 HTML·JS 임시 파일만 C:\Users\hwan\AppData\Local\Temp\claude\98e919c71070586c\d418693f-06fd-403b-ba28-04a0db072584\scratchpad 아래에 남아 있다.)

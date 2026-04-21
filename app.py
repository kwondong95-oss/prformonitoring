import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import time
import urllib3
import urllib.parse  
import re  
import os 
import google.generativeai as genai

# 보안 인증서 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# [🔥 핵심 업데이트] NDSoft 공통 시스템 명단에 '충남인터넷뉴스' 추가! (총 10곳)
NDSOFT_GROUP = [
    "중부매일", "충청신문", "충남팩트뉴스", "중앙매일", 
    "투데이충남", "충남뉴스통신", "메가충청뉴스", "로컬투데이",
    "대전투데이", "충남인터넷뉴스" 
]

# --- 1. 언론사 맞춤형 기사 링크 추출기 ---
def get_article_links(soup, media_name, base_url):
    """사용자가 제공한 언론사별 HTML 구조에 맞춰 1:1로 기사 링크만 추출합니다."""
    valid_links = []
    seen = set()
    clean_base = str(base_url).rstrip('/')
    
    for a in soup.find_all('a', href=True):
        href = a['href']
        title = a.get_text(separator=' ', strip=True)
        if not title or len(title) < 3:
            continue
            
        is_match = False
        
        # [구조 1] 시대일보, e당진뉴스, 충남신문: 숫자만 있는 주소 
        if media_name in ["시대일보", "e당진뉴스", "충남신문"]:
            if re.search(r'^\/\d{4,8}$', href):
                is_match = True
                
        # [구조 2] 충청탑뉴스
        elif media_name == "충청탑뉴스":
            if "aid=" in href or ("class" in a.attrs and "sublist" in a.get("class", [])):
                is_match = True
                
        # [구조 3] 충남뉴스통신 (NDSoft지만 별도 클래스 있을 경우 대비)
        elif media_name == "충남뉴스통신":
            if "idxno=" in href or ("class" in a.attrs and "links" in a.get("class", [])):
                is_match = True
                
        # [구조 4] 당진투데이
        elif media_name == "당진투데이":
            if a.find(class_='title') or re.search(r'(idxno=|no=|seq=|idx=)', href, re.IGNORECASE):
                is_match = True
                
        # [구조 5] 그 외 NDSoft 기반 (충남인터넷뉴스 포함 10곳): idxno= 포함
        else:
            if "idxno=" in href:
                is_match = True
                
        if is_match:
            abs_link = href if href.startswith('http') else f"{clean_base}/{href.lstrip('/')}"
            if abs_link not in seen:
                seen.add(abs_link)
                valid_links.append({"title": title, "link": abs_link})
                
    return valid_links

# --- 2. 데이터 추출 및 정제 유틸리티 ---
def build_search_url(media_name, base_url, csv_url, keyword):
    clean_base = str(base_url).rstrip('/')
    utf8_k = urllib.parse.quote(keyword.encode('utf-8'))
    euckr_k = urllib.parse.quote(keyword.encode('euc-kr'))

    # NDSoft 그룹은 CSV 주소가 빈칸이거나 잘못되어도 완벽한 주소로 강제 조립
    if media_name in NDSOFT_GROUP:
        return f"{clean_base}/news/articleList.html?sc_area=A&view_type=sm&sc_word={utf8_k}"
        
    if pd.isna(csv_url) or not str(csv_url).strip():
        return f"{clean_base}/news/articleList.html?sc_area=A&view_type=sm&sc_word={utf8_k}"
    
    url = str(csv_url)
    url = url.replace("현대제철", keyword)
    url = url.replace("%ED%98%84%EB%8C%80%EC%A0%9C%EC%B2%A0", utf8_k)
    url = url.replace("%C7%F6%B4%EB%C1%A6%C3%B6", euckr_k)
    url = url.replace("%ED%98%84%E3%84%B7%EC%9E%AC%EC%B2%B4%EB%9F%AC", utf8_k)
    return url

def extract_article_date(soup):
    """본문(상세 페이지)에서 유효한 기사 발행일만 정밀하게 타겟팅하여 추출합니다."""
    
    # 1순위: 기사 상단 정보 영역에서 '승인' 또는 '입력' 날짜를 가장 먼저 찾음 (가장 정확한 화면 표시 날짜)
    header_area = soup.select_one('.info_line, .article-head, .view-info, .news_info, .date_wrap, .date, .list_date, .byline, .info-text')
    if header_area:
        match = re.search(r'(?:승인|입력|등록|작성일|기사출고)\s*[:\s]*\w*\s*(20[12]\d)\s*[-./년]\s*(0?[1-9]|1[0-2])\s*[-./월]\s*(0?[1-9]|[12]\d|3[01])', header_area.text)
        if match:
            y, m, d = map(int, match.groups())
            try: return datetime(y, m, d).date(), f"{y}-{m:02d}-{d:02d}"
            except ValueError: pass
            
        match = re.search(r'(20[12]\d)\s*[-./년]\s*(0?[1-9]|1[0-2])\s*[-./월]\s*(0?[1-9]|[12]\d|3[01])', header_area.text)
        if match:
            y, m, d = map(int, match.groups())
            try: return datetime(y, m, d).date(), f"{y}-{m:02d}-{d:02d}"
            except ValueError: pass

    # 2순위: 기사 메타 데이터 (화면 표시 날짜를 못 찾았을 때의 보루)
    meta_date = soup.find('meta', property='article:published_time') or soup.find('meta', attrs={'name': 'article:published_time'})
    if meta_date and meta_date.get('content'):
        match = re.search(r'(20[12]\d)[-./](0?[1-9]|1[0-2])[-./](0?[1-9]|[12]\d|3[01])', meta_date['content'])
        if match:
            y, m, d = map(int, match.groups())
            return datetime(y, m, d).date(), f"{y}-{m:02d}-{d:02d}"

    # 3순위: 본문 최상단에서 직관적인 날짜 패턴 추출
    main_container = soup.select_one('#article-view-content-div, .article-body, #articleBody, #news_body_area, .txt_box, .view_cont')
    search_text = main_container.text if main_container else soup.text[:2000]
    
    match = re.search(r'(?:승인|입력|등록|작성일|기사출고)\s*[:\s]*\w*\s*(20[12]\d)\s*[-./년]\s*(0?[1-9]|1[0-2])\s*[-./월]\s*(0?[1-9]|[12]\d|3[01])', search_text)
    if match:
        y, m, d = map(int, match.groups())
        try: return datetime(y, m, d).date(), f"{y}-{m:02d}-{d:02d}"
        except ValueError: pass
            
    match = re.search(r'(20[12]\d)\s*[-./년]\s*(0?[1-9]|1[0-2])\s*[-./월]\s*(0?[1-9]|[12]\d|3[01])', search_text)
    if match:
        y, m, d = map(int, match.groups())
        try: return datetime(y, m, d).date(), f"{y}-{m:02d}-{d:02d}"
        except ValueError: pass

    return None, "날짜 파싱 오류"

def extract_reporter(soup):
    info_area = soup.select('.info_line, .info-text, .date, .byline, .list_date, .time, .reporter, .writer, .view-info')
    for tag in info_area:
        clean_text = re.sub(r'[\[\]|()<>{}]', ' ', tag.text)
        match = re.search(r'([가-힣]{2,4})\s*(기자|특파원)', clean_text)
        if match:
            return match.group(1).strip()
    
    clean_text = re.sub(r'[\[\]|()<>{}]', ' ', soup.text)
    match = re.search(r'([가-힣]{2,4})\s*(기자|특파원)', clean_text)
    if match: 
        return match.group(1).strip()
    
    return "기자 정보 없음"

def summarize_with_gemini(text, api_key):
    if not text: return "본문 데이터 없음"
    if not api_key: return text.strip()[:200] + "..." if len(text) > 200 else text.strip()
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"다음 기사를 3문장 이내의 명확한 비즈니스 요약본으로 작성하십시오:\n\n{text[:3000]}"
        return model.generate_content(prompt).text.strip()
    except Exception as e:
        return f"[AI 요약 실패] {e}"

# --- 3. 크롤링 메인 프로세스 ---
def scrape_news(media_name, base_url, csv_search_url, keyword, start_date, end_date, gemini_api_key):
    logs = []
    results = []
    
    search_url = build_search_url(media_name, base_url, csv_search_url, keyword)
    if not search_url: return results, logs

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'} 
    
    try:
        response = requests.get(search_url, headers=headers, timeout=10, verify=False)
        response.raise_for_status()
        
        # 억지 디코딩 방지 (EUC-KR 깨짐 차단)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        article_links = get_article_links(soup, media_name, base_url)
        
        if not article_links:
            logs.append(f"[WARN] {media_name} - 검색된 기사가 없거나 HTML 구조가 일치하지 않습니다.")
            return results, logs

        logs.append(f"[INFO] {media_name} - 기사 링크 {len(article_links)}건 탐지. 상세 분석 시작.")

        for item in article_links:
            title = item['title']
            link = item['link']
            
            try:
                art_res = requests.get(link, headers=headers, timeout=5, verify=False)
                art_soup = BeautifulSoup(art_res.content, 'html.parser')
                
                content_tag = art_soup.select_one("#article-view-content-div, .article-body, #articleBody, #news_body_area, .txt_box, .view_cont")
                content_text = content_tag.text.strip() if content_tag else art_soup.text.strip()
                
                # [필터 1] 키워드 검증
                if keyword not in title and keyword not in content_text:
                    continue
                
                # [필터 2] 발행일자 검증
                dt_obj, date_str = extract_article_date(art_soup)
                if dt_obj:
                    if not (start_date <= dt_obj <= end_date):
                        continue 
                else:
                    continue # 날짜를 알 수 없는 기사는 배제

                reporter = extract_reporter(art_soup)
                summary = summarize_with_gemini(content_text, gemini_api_key)

                # CSV 깨짐 방지 레이아웃
                clean_title = title.replace('\n', ' ').replace('\r', '').strip()
                clean_summary = summary.replace('\n', ' ').replace('\r', '').strip()
                clean_reporter = reporter.replace('\n', '').strip()

                results.append({
                    "언론사": media_name,
                    "게시일자": date_str,
                    "제목": clean_title,
                    "기자": clean_reporter,
                    "요약내용": clean_summary,
                    "링크": link
                })
                time.sleep(0.2)

            except Exception as e:
                continue
                
        logs.append(f"[SUCCESS] {media_name} - 수집 및 필터링 완료 (최종 수집: {len(results)}건)")
            
    except Exception as e:
        logs.append(f"[ERROR] {media_name} - 프로세스 오류: {e}")
        
    return results, logs 

# --- 4. Streamlit UI 구성 ---
st.set_page_config(page_title="지역 언론사 모니터링 시스템", layout="wide")
st.title("지역 언론사 대상 뉴스 모니터링 자동화 시스템")

st.sidebar.header("시스템 설정")
gemini_api_key = st.sidebar.text_input("Gemini API Key (선택)", type="password", help="AI 기반 요약 기능을 활성화하려면 API Key를 입력하십시오.")

default_csv_path = "언론사 홈페이지.csv"
df_media = None

if os.path.exists(default_csv_path):
    try:
        df_media = pd.read_csv(default_csv_path, encoding='utf-8')
    except UnicodeDecodeError:
        df_media = pd.read_csv(default_csv_path, encoding='cp949')
    st.sidebar.success(f"기준 데이터베이스 연동 완료")
else:
    st.sidebar.warning(f"작업 디렉토리 내 '{default_csv_path}' 파일이 존재하지 않습니다.")
    uploaded_file = st.sidebar.file_uploader("언론사 목록 데이터 업로드", type=['csv'])
    if uploaded_file:
        try:
            df_media = pd.read_csv(uploaded_file, encoding='utf-8')
        except UnicodeDecodeError:
            uploaded_file.seek(0)
            df_media = pd.read_csv(uploaded_file, encoding='cp949')

keyword = st.sidebar.text_input("분석 대상 키워드", placeholder="예: 현대제철, 당진시", value="현대제철")
col1, col2 = st.sidebar.columns(2)
start_date = col1.date_input("수집 범위 (시작일)")
end_date = col2.date_input("수집 범위 (종료일)")

if st.sidebar.button("모니터링 데이터 수집 실행", type="primary"):
    if df_media is None or '검색주소창' not in df_media.columns:
        st.sidebar.error("기준 언론사 데이터 구조가 올바르지 않습니다.")
    elif not keyword:
        st.sidebar.error("분석 대상 키워드를 입력해 주십시오.")
    else:
        with st.spinner("대상 언론사 심층 파싱 및 데이터 필터링을 진행 중입니다..."):
            all_news_data = []
            all_logs = [] 
            total_media = len(df_media)
            
            progress_bar = st.progress(0, text="프로세스 초기화 중...")
            
            for index, row in df_media.iterrows():
                media_name = row['구분']
                base_url = row['홈페이지 주소']
                csv_search_url = row['검색주소창'] if '검색주소창' in df_media.columns else None
                
                progress_bar.progress((index + 1) / total_media, text=f"수집 프로세스 진행 중: {media_name} ({index + 1}/{total_media})")
                
                news_data, logs = scrape_news(media_name, base_url, csv_search_url, keyword, start_date, end_date, gemini_api_key)
                
                all_news_data.extend(news_data)
                all_logs.extend(logs) 
            
            progress_bar.empty() 
        
        if all_news_data:
            st.success(f"프로세스 완료. 총 {len(all_news_data)}건의 유효 기사가 추출되었습니다.")
            
            # [🔥 핵심 업데이트] DataFrame 생성 후 '게시일자' 기준 오름차순(과거->최신) 정렬
            result_df = pd.DataFrame(all_news_data)
            result_df = result_df.sort_values(by='게시일자', ascending=True).reset_index(drop=True)
            
            st.dataframe(result_df, use_container_width=True)
            
            # 엑셀에서 한글이 깨지지 않도록 utf-8-sig 인코딩 보장
            csv = result_df.to_csv(index=False, encoding='utf-8-sig')
            st.download_button(
                label="추출 결과 리포트 다운로드 (CSV)",
                data=csv.encode('utf-8-sig'),
                file_name=f"Monitoring_Report_{keyword}_{start_date}~{end_date}.csv",
                mime='text/csv',
            )
        else:
            st.info(f"지정된 기간 내 '{keyword}' 키워드와 연관된 유효 기사가 검출되지 않았습니다.")
            
        if all_logs:
            with st.expander("시스템 프로세스 로그 (디버깅용)"):
                log_text = "\n".join(all_logs)
                st.text_area("System Logs", value=log_text, height=300)
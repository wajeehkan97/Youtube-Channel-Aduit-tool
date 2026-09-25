import streamlit as st
import pandas as pd
import re
import json
from datetime import datetime, timezone
from googleapiclient.discovery import build
import isodate

st.set_page_config(page_title="YouTube Trust Score", page_icon="📊", layout="wide")

st.title("📊 YouTube Trust Score & Improvement Tool")
st.caption("Bread + Sonion Labs | YouTube Data API v3 based heuristic")

with st.sidebar:
    st.header("Setup")
    api_key = st.text_input("YouTube Data API Key", type="password")
    max_videos = st.slider("How many latest videos to analyze", 10, 50, 50)
    st.markdown("---")
    st.markdown("**How to get an API Key?**")
    st.markdown("""
1. Create a Google Cloud Project  
2. Enable YouTube Data API v3  
3. Create an API Key  
    """)

channel_input = st.text_input(
    "Channel link / ID / @handle",
    placeholder="https://www.youtube.com/@YourChannel"
)

def extract_channel_id(youtube, text):
    text = text.strip()

    m = re.search(r'channel/(UC[\w-]{22})', text)
    if m:
        return m.group(1)

    if re.fullmatch(r'UC[\w-]{22}', text):
        return text

    m = re.search(r'@([\w.-]+)', text)
    if m:
        handle = m.group(1)
        try:
            res = youtube.search().list(
                part='snippet',
                q=handle,
                type='channel',
                maxResults=1
            ).execute()
            if res.get('items'):
                return res['items'][0]['snippet']['channelId']
        except Exception:
            pass

    m = re.search(r'user/([^/?&]+)', text)
    if m:
        try:
            res = youtube.channels().list(
                part='id',
                forUsername=m.group(1)
            ).execute()
            if res.get('items'):
                return res['items'][0]['id']
        except Exception:
            pass

    try:
        res = youtube.search().list(
            part='snippet',
            q=text,
            type='channel',
            maxResults=1
        ).execute()
        if res.get('items'):
            return res['items'][0]['snippet']['channelId']
    except Exception:
        pass

    return None

def get_channel(youtube, cid):
    res = youtube.channels().list(
        part='snippet,statistics,brandingSettings,contentDetails,topicDetails,status',
        id=cid
    ).execute()
    if not res.get('items'):
        return None
    return res['items'][0]

def get_videos(youtube, uploads_id, max_videos=50):
    items = []
    next_page = None

    while len(items) < max_videos:
        res = youtube.playlistItems().list(
            part='contentDetails',
            playlistId=uploads_id,
            maxResults=min(50, max_videos - len(items)),
            pageToken=next_page
        ).execute()

        items.extend(res.get('items', []))
        next_page = res.get('nextPageToken')

        if not next_page:
            break

    video_ids = [it['contentDetails']['videoId'] for it in items]
    videos = []

    for i in range(0, len(video_ids), 50):
        res = youtube.videos().list(
            part='snippet,statistics,contentDetails',
            id=','.join(video_ids[i:i+50])
        ).execute()
        videos.extend(res.get('items', []))

    return videos

def parse_duration(iso):
    try:
        return isodate.parse_duration(iso).total_seconds()
    except Exception:
        return 0

def analyze(channel, videos):
    stats = channel['statistics']
    snippet = channel['snippet']
    branding = channel.get('brandingSettings', {}).get('channel', {})

    subs = int(stats.get('subscriberCount', 0))
    total_views = int(stats.get('viewCount', 0))
    video_count = int(stats.get('videoCount', 0))

    created = datetime.fromisoformat(snippet['publishedAt'].replace('Z', '+00:00'))
    age_days = (datetime.now(timezone.utc) - created).days

    rows = []

    for v in videos:
        s = v['snippet']
        stt = v['statistics']
        cd = v['contentDetails']

        published = datetime.fromisoformat(s['publishedAt'].replace('Z', '+00:00'))
        views = int(stt.get('viewCount', 0))
        likes = int(stt.get('likeCount', 0))
        comments = int(stt.get('commentCount', 0))
        duration = parse_duration(cd['duration'])

        title = s.get('title', '')
        desc = s.get('description', '')
        tags = s.get('tags', [])

        rows.append({
            'id': v['id'],
            'title': title,
            'published': published,
            'views': views,
            'likes': likes,
            'comments': comments,
            'duration': duration,
            'title_len': len(title),
            'desc_len': len(desc),
            'tags_count': len(tags),
            'is_short': duration <= 60 or '#shorts' in title.lower() or '#shorts' in desc.lower(),
            'engagement': (likes + comments) / views if views > 0 else 0,
            'like_rate': likes / views if views > 0 else 0,
            'comment_rate': comments / views if views > 0 else 0,
        })

    df = pd.DataFrame(rows)

    if df.empty:
        return None, None, None, None

    df = df.sort_values('published').reset_index(drop=True)

    avg_views = df['views'].mean()
    median_views = df['views'].median()
    avg_engagement = df['engagement'].mean() * 100
    avg_like_rate = df['like_rate'].mean() * 100
    avg_comment_rate = df['comment_rate'].mean() * 100
    views_per_sub = avg_views / subs if subs > 0 else 0

    days_since_last = (datetime.now(timezone.utc) - df['published'].max()).days

    uploads_last_30 = len(
        df[df['published'] >= datetime.now(timezone.utc) - pd.Timedelta(days=30)]
    )

    intervals = df['published'].diff().dt.total_seconds().dropna() / 86400
    avg_interval = intervals.mean() if len(intervals) > 0 else 0
    std_interval = intervals.std() if len(intervals) > 1 else 0

    shorts_ratio = df['is_short'].mean()
    avg_title_len = df['title_len'].mean()
    avg_desc_len = df['desc_len'].mean()
    avg_tags = df['tags_count'].mean()
    avg_duration = df['duration'].mean() / 60

    if len(df) >= 20:
        recent = df.tail(10)['views'].mean()
        older = df.iloc[-20:-10]['views'].mean()
        trend = recent / older if older > 0 else 1
    elif len(df) >= 10:
        recent = df.tail(5)['views'].mean()
        older = df.head(5)['views'].mean()
        trend = recent / older if older > 0 else 1
    else:
        trend = 1

    clickbait_words = [
        'shocking', 'insane', "you won't believe", 'free', 'hack',
        '100%', 'guaranteed', 'secret', 'unbelievable', 'crazy',
        'mind blowing', 'viral'
    ]

    clickbait_count = df['title'].str.lower().apply(
        lambda x: any(w in x for w in clickbait_words)
    ).mean()

    norm_titles = (
        df['title']
        .str.lower()
        .str.replace(r'[^a-z0-9 ]', '', regex=True)
        .str.strip()
    )
    dup_ratio = norm_titles.duplicated().mean()

    score = 0
    breakdown = {}

    # 1. Channel Completeness - 10
    c = 0
    if len(snippet.get('description', '')) > 100:
        c += 2
    if snippet.get('country'):
        c += 1
    if branding.get('customUrl'):
        c += 1
    if branding.get('bannerExternalUrl'):
        c += 2
    if branding.get('keywords'):
        c += 1
    if subs > 100:
        c += 1
    if video_count > 10:
        c += 2
    c = min(c, 10)
    score += c
    breakdown['Channel Completeness'] = c

    # 2. Upload Consistency - 15
    c = 0
    if days_since_last <= 7:
        c += 5
    elif days_since_last <= 14:
        c += 3
    elif days_since_last <= 30:
        c += 1

    if uploads_last_30 >= 8:
        c += 5
    elif uploads_last_30 >= 4:
        c += 3
    elif uploads_last_30 >= 1:
        c += 1

    if std_interval <= 3:
        c += 5
    elif std_interval <= 7:
        c += 3
    elif std_interval <= 14:
        c += 1

    c = min(c, 15)
    score += c
    breakdown['Upload Consistency'] = c

    # 3. Engagement Quality - 20
    if avg_engagement >= 5:
        c = 20
    elif avg_engagement >= 3:
        c = 15
    elif avg_engagement >= 1.5:
        c = 10
    elif avg_engagement >= 0.8:
        c = 5
    else:
        c = 0
    score += c
    breakdown['Engagement Quality'] = c

    # 4. Views per Subscriber - 15
    if views_per_sub >= 1.0:
        c = 15
    elif views_per_sub >= 0.5:
        c = 12
    elif views_per_sub >= 0.2:
        c = 8
    elif views_per_sub >= 0.1:
        c = 4
    else:
        c = 0
    score += c
    breakdown['Views per Subscriber'] = c

    # 5. Metadata Quality - 15
    c = 0
    if 30 <= avg_title_len <= 70:
        c += 5
    else:
        c += 2

    if avg_desc_len >= 200:
        c += 5
    elif avg_desc_len >= 100:
        c += 3
    elif avg_desc_len > 0:
        c += 1

    if avg_tags >= 8:
        c += 5
    elif avg_tags >= 4:
        c += 3
    elif avg_tags > 0:
        c += 1

    c = min(c, 15)
    score += c
    breakdown['Metadata Quality'] = c

    # 6. Content Mix - 10
    c = 0
    if 0.2 <= shorts_ratio <= 0.6:
        c += 5
    else:
        c += 2

    if avg_duration >= 3:
        c += 5
    else:
        c += 2

    c = min(c, 10)
    score += c
    breakdown['Content Mix'] = c

    # 7. Growth Momentum - 10
    if trend >= 1.2:
        c = 10
    elif trend >= 1.0:
        c = 7
    elif trend >= 0.8:
        c = 4
    else:
        c = 0
    score += c
    breakdown['Growth Momentum'] = c

    # 8. Policy / Clickbait Risk - 5
    if clickbait_count < 0.1 and dup_ratio < 0.1:
        c = 5
    elif clickbait_count < 0.3 and dup_ratio < 0.2:
        c = 3
    else:
        c = 0
    score += c
    breakdown['Policy/Clickbait Risk'] = c

    metrics = {
        'subscribers': subs,
        'total_views': total_views,
        'video_count': video_count,
        'channel_age_days': age_days,
        'avg_views': avg_views,
        'median_views': median_views,
        'avg_engagement': avg_engagement,
        'avg_like_rate': avg_like_rate,
        'avg_comment_rate': avg_comment_rate,
        'views_per_sub': views_per_sub,
        'days_since_last': days_since_last,
        'uploads_last_30': uploads_last_30,
        'avg_interval_days': avg_interval,
        'std_interval_days': std_interval,
        'shorts_ratio': shorts_ratio,
        'avg_title_len': avg_title_len,
        'avg_desc_len': avg_desc_len,
        'avg_tags': avg_tags,
        'avg_duration_min': avg_duration,
        'trend_ratio': trend,
        'clickbait_ratio': clickbait_count,
        'duplicate_title_ratio': dup_ratio,
    }

    return score, breakdown, metrics, df

def get_recommendations(score, breakdown, m):
    recs = []

    if breakdown['Channel Completeness'] < 7:
        recs.append("🔧 Complete your channel about, links, banner, keywords, and custom URL.")

    if breakdown['Upload Consistency'] < 12:
        recs.append("📅 Fix your upload schedule: 2-3 videos per week at the same time. Use batch recording.")

    if breakdown['Engagement Quality'] < 12:
        recs.append("💬 Boost engagement: ask a question in the first 30 seconds, pin a comment, use end screens, and community posts.")

    if breakdown['Views per Subscriber'] < 8:
        recs.append("🎯 Improve CTR: use curiosity + keywords in titles, max 3 words on thumbnails, include face/emotion.")

    if breakdown['Metadata Quality'] < 10:
        recs.append("📝 Write 200+ word descriptions, add 8-12 relevant tags, timestamps, links, and hashtags.")

    if breakdown['Content Mix'] < 6:
        recs.append("🎬 Mix Shorts + Long-form: 40% Shorts, 60% long-form; hook viewers from Shorts to long videos.")

    if breakdown['Growth Momentum'] < 7:
        recs.append("📈 Check your top 10 videos in Analytics and create sequels/remakes.")

    if breakdown['Policy/Clickbait Risk'] < 3:
        recs.append("⚠️ Reduce clickbait and duplicate titles; misleading content increases trust and monetization risk.")

    if m['avg_comment_rate'] < 0.2:
        recs.append("❤️ Reply to comments and use polls/community posts to increase interaction.")

    if m['avg_duration_min'] < 2:
        recs.append("⏱️ Keep videos 3-8 minutes long, add a pattern break every 20-30 seconds.")

    if m['days_since_last'] > 14:
        recs.append("🚀 No upload in 14+ days; send an active signal to the algorithm by posting a Short/Video today.")

    if not recs:
        recs.append("✅ Great job! Now focus on A/B thumbnail testing, retention graphs, and audience surveys.")

    return recs

if st.button("Analyze Channel", type="primary"):
    if not api_key:
        st.error("Please enter your API Key")
    elif not channel_input:
        st.error("Please enter a Channel link/ID")
    else:
        try:
            youtube = build('youtube', 'v3', developerKey=api_key)

            with st.spinner("Resolving channel..."):
                cid = extract_channel_id(youtube, channel_input)

            if not cid:
                st.error("Channel ID not found. Please check the link.")
                st.stop()

            with st.spinner("Fetching channel data..."):
                channel = get_channel(youtube, cid)

            if not channel:
                st.error("Channel data not found")
                st.stop()

            uploads_id = channel['contentDetails']['relatedPlaylists']['uploads']

            with st.spinner("Analyzing videos..."):
                videos = get_videos(youtube, uploads_id, max_videos)

            score, breakdown, m, df = analyze(channel, videos)

            if score is None:
                st.error("No public videos found")
                st.stop()

            st.success(f"Channel: {channel['snippet']['title']}")

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Trust Score", f"{score}/100")
            col2.metric("Subscribers", f"{m['subscribers']:,}")
            col3.metric("Avg Views", f"{m['avg_views']:,.0f}")
            col4.metric("Engagement", f"{m['avg_engagement']:.2f}%")

            st.progress(min(score / 100, 1.0))

            if score >= 85:
                label = "Excellent Trust"
            elif score >= 70:
                label = "Good Trust"
            elif score >= 50:
                label = "Average Trust"
            elif score >= 30:
                label = "Needs Work"
            else:
                label = "High Risk"

            st.subheader(f"Rating: {label}")

            st.subheader("📊 Score Breakdown")
            max_map = {
                'Channel Completeness': 10,
                'Upload Consistency': 15,
                'Engagement Quality': 20,
                'Views per Subscriber': 15,
                'Metadata Quality': 15,
                'Content Mix': 10,
                'Growth Momentum': 10,
                'Policy/Clickbait Risk': 5,
            }

            bdf = pd.DataFrame(
                list(breakdown.items()),
                columns=['Category', 'Score']
            )
            bdf['Max'] = bdf['Category'].map(max_map)
            bdf['Percent'] = (bdf['Score'] / bdf['Max'] * 100).round(1)
            st.dataframe(bdf, use_container_width=True)

            st.subheader("📈 Detailed Metrics")
            metrics_display = {
                'Channel Age (days)': m['channel_age_days'],
                'Total Views': f"{m['total_views']:,}",
                'Video Count': f"{m['video_count']:,}",
                'Avg Views': f"{m['avg_views']:,.0f}",
                'Median Views': f"{m['median_views']:,.0f}",
                'Avg Engagement': f"{m['avg_engagement']:.2f}%",
                'Like Rate': f"{m['avg_like_rate']:.2f}%",
                'Comment Rate': f"{m['avg_comment_rate']:.2f}%",
                'Views per Sub': f"{m['views_per_sub']:.2f}",
                'Days Since Last Upload': m['days_since_last'],
                'Uploads Last 30 Days': m['uploads_last_30'],
                'Avg Upload Interval (days)': f"{m['avg_interval_days']:.1f}",
                'Std Interval (days)': f"{m['std_interval_days']:.1f}",
                'Shorts Ratio': f"{m['shorts_ratio'] * 100:.1f}%",
                'Avg Title Length': f"{m['avg_title_len']:.1f}",
                'Avg Description Length': f"{m['avg_desc_len']:.0f}",
                'Avg Tags': f"{m['avg_tags']:.1f}",
                'Avg Duration (min)': f"{m['avg_duration_min']:.1f}",
                'Trend Ratio': f"{m['trend_ratio']:.2f}",
                'Clickbait Ratio': f"{m['clickbait_ratio'] * 100:.1f}%",
                'Duplicate Title Ratio': f"{m['duplicate_title_ratio'] * 100:.1f}%",
            }

            st.table(
                pd.DataFrame(
                    metrics_display.items(),
                    columns=['Metric', 'Value']
                )
            )

            st.subheader("🏆 Top 10 Videos")
            top = df.nlargest(10, 'views')[
                ['title', 'views', 'likes', 'comments', 'published']
            ]
            st.dataframe(top, use_container_width=True)

            st.subheader("⚠️ Low Performing Videos")
            low = df.nsmallest(10, 'views')[
                ['title', 'views', 'likes', 'comments', 'published']
            ]
            st.dataframe(low, use_container_width=True)

            st.subheader("🛠️ Improvement Plan")
            recs = get_recommendations(score, breakdown, m)
            for r in recs:
                st.write(r)

            report = {
                'channel': channel['snippet']['title'],
                'channel_id': cid,
                'score': score,
                'breakdown': breakdown,
                'metrics': m,
                'recommendations': recs,
                'videos': df.to_dict(orient='records')
            }

            def default(o):
                if isinstance(o, datetime):
                    return o.isoformat()
                return str(o)

            st.download_button(
                "📥 Download JSON Report",
                data=json.dumps(report, default=default, indent=2),
                file_name=f"trust_report_{cid}.json",
                mime="application/json"
            )

        except Exception as e:
            st.error(f"Error: {e}")
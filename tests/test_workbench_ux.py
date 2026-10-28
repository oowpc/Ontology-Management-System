"""M30 · 前端以用户为中心：角色工作台落地页 / 导航分组过滤 / session flash / 友好错误页 / 复核快捷键。"""
from conftest import login


def test_workbench_by_role(client):
    """落地页：全员看到自己的组今日上传状态；审核员有待办队列；admin 有系统概况。"""
    login(client, "annotator")
    r = client.get("/")
    assert r.status_code == 200 and "工作台" in r.text
    assert "我的题目" in r.text and "我的组" in r.text      # 标注员视角
    assert "待办队列" not in r.text                          # 审核员区块不出现

    login(client, "reviewer")
    r = client.get("/")
    assert "待办队列" in r.text and "待审提交" in r.text
    assert "系统概况" not in r.text                          # admin 区块不出现

    login(client, "admin")
    r = client.get("/")
    assert "待办队列" in r.text and "系统概况" in r.text


def test_nav_role_filtering(client):
    """无权限的导航项直接隐藏（不再是灰块占位）。"""
    login(client, "annotator")
    html = client.get("/").text
    assert 'href="/admin/users"' not in html
    assert 'href="/ke/review"' not in html
    assert 'href="/io"' not in html
    assert 'href="/questions"' in html and 'href="/ke/upload"' in html

    login(client, "admin")
    html = client.get("/").text
    assert 'href="/admin/users"' in html and 'href="/ke/review"' in html and 'href="/io"' in html
    # GOLD 重复链接已修：/gold 出现且只有一处（成品库是 /gold/items）
    assert html.count('href="/gold"') == 1


def test_session_flash_once(client):
    """session flash：渲染一次即清，刷新不重现（区别于 ?ok= 拼 URL）。"""
    login(client, "admin")
    from app.db import SessionLocal
    from app.models import Concept
    db = SessionLocal()
    c = Concept(name="闪信验证概念", domain="通用", status="approved", created_by="test")
    db.add(c)
    db.commit()
    cid = c.id
    db.close()

    r = client.post(f"/concepts/{cid}/disable", follow_redirects=False)
    assert r.status_code == 303 and "?" not in r.headers["location"]   # URL 不再带消息
    r = client.get(f"/concepts/{cid}")
    assert "已停用" in r.text                                          # 首次渲染出现
    r = client.get(f"/concepts/{cid}")
    assert "已停用</div>" not in r.text                                # 刷新即消失
    r = client.post(f"/concepts/{cid}/restore", follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"/concepts/{cid}")
    assert "已恢复" in r.text


def test_error_pages(client):
    """页面路径的 404/403 渲染友好错误页；/api/ 保持 JSON 契约。"""
    login(client, "annotator")
    r = client.get("/concepts/999999")
    assert r.status_code == 404 and "text/html" in r.headers["content-type"]
    assert "回到工作台" in r.text
    r = client.get("/api/concepts/999999")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    # 403（无权限页面）也是人看的页面
    r = client.get("/health")
    assert r.status_code == 403 and "权限不足" in r.text


def test_review_keyboard_shortcuts(client):
    login(client, "reviewer")
    r = client.get("/ke/review")
    assert r.status_code == 200
    assert "row-focus" in r.text and "keydown" in r.text

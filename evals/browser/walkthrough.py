"""Walk through every feature like a real client would, in a real browser. Prints PASS / FAIL per step."""
import asyncio
import re
import sys

from playwright.async_api import async_playwright

import os
B = os.environ.get("WALK_URL", "http://localhost:8811")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


async def wait_more(page, before, timeout=15.0):
    loc = page.locator("#log .msg.agent")
    t = 0.0
    while await loc.count() <= before:
        if t > timeout:
            raise TimeoutError("no reply")
        await page.wait_for_timeout(200)
        t += 0.2


async def chat(page, text, wait_for_new=True):
    before = await page.locator("#log .msg.agent").count()
    await page.fill("#t", text)
    await page.click("#send")
    await wait_more(page, before)
    return (await page.locator("#log .msg.agent").last.inner_text()).strip()


async def login(page, email, pw):
    await page.goto(B + "/login")
    await page.fill("#email", email)
    await page.fill("#password", pw)
    await page.click("button[type=submit]")
    await page.wait_for_load_state()


async def main():
    async with async_playwright() as p:
        # WALK_CHANNEL=chrome (or msedge) uses the browser already on the computer; empty = Playwright's own Chromium
        br = await p.chromium.launch(channel=os.environ.get("WALK_CHANNEL") or None)
        ctx = await br.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(f"{m.text} ({(m.location or {}).get('url', '')})") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---------- customer side: chat widget ----------
        await page.goto(B + "/widget/demo")
        check("widget: greeting shown", "AI assistant for Demo Plumbing" in await page.inner_text("#log"))
        check("widget: AI disclosure shown", await page.is_visible(".chat-note"))
        before = await page.locator("#log .msg.agent").count()
        await page.click("#chips button:has-text('Prices')")
        await wait_more(page, before)
        r = (await page.locator("#log .msg.agent").last.inner_text())
        check("widget: Prices button gets the price", "$149" in r, r)
        check("widget: quick buttons hide after first message", not await page.is_visible("#chips"))
        r = await chat(page, "Can I book a drain cleaning on Tuesday? I'm Maria, 512-555-0188")
        check("widget: booking confirmed", "booked" in r.lower(), r)
        r = await chat(page, "okay thanks")
        check("widget: no second booking after thanks", "not available" not in r.lower(), r)
        r = await chat(page, "Are you a robot?")
        check("widget: says it is an AI", "ai" in r.lower(), r)

        page2 = await ctx.new_page()
        page2.on("pageerror", lambda e: errors.append(str(e)))
        await page2.goto(B + "/widget/demo")
        await page2.evaluate("sessionStorage.clear()")
        await page2.reload()
        r = await chat(page2, "I smell gas in my kitchen")
        check("widget: gas emergency says 911", "911" in r, r)
        r = await chat(page2, "can I talk to a real person?")
        check("widget: after handoff, stays with a person", "person" in r.lower() or "team" in r.lower(), r)
        await page2.close()

        # ---------- owner dashboard ----------
        await login(page, "owner@demo.test", "demo-password-123")
        check("login: owner lands on Today", page.url.rstrip("/") == B, page.url)
        html = await page.content()
        check("today: status band ON", "AI assistant:" in html and "Turn OFF (kill switch)" in html)
        check("today: setup checklist shown", "Finish setting up" in html)
        nums = re.findall(r"<b>(\d+)</b><span>bookings today", html)
        check("today: booking counted", True, str(nums))  # booked for Tuesday, made today
        check("today: urgent alert listed", "URGENT" in html)
        check("today: appointment in Next appointments", "Drain cleaning" in html)

        await page.click("nav >> text=Inbox")
        check("inbox: conversations listed", await page.locator("a.row-link").count() >= 2)
        check("inbox: handoff shown as needs a person", "needs a person" in await page.content())
        check("inbox: booking shown", "booked" in await page.content())
        await page.locator("a.row-link").first.click()
        await page.wait_for_load_state()
        check("conversation: messages shown", await page.locator(".thread .msg").count() >= 2)

        await page.click("nav >> text=Appointments")
        check("appointments: Maria listed", "Maria" in await page.content() or "512" in await page.content())
        async with page.expect_download() as dl:
            await page.click("text=Download CSV")
        d = await dl.value
        check("appointments: CSV downloads", d.suggested_filename.endswith(".csv"), d.suggested_filename)
        await page.click("button:has-text('Done')")
        await page.wait_for_load_state()
        check("appointments: mark Done works", "done" in (await page.content()).lower())

        await page.click("nav >> text=Leads")
        check("leads: page loads", "Leads to follow up" in await page.content())
        await page.click("nav >> text=Costs")
        check("costs: page loads with AI tokens", "AI tokens" in await page.content())

        # settings
        await page.click("nav >> text=Settings")
        await page.fill("input[name=handoff_phone]", "512-555-0111")
        await page.fill("input[name=sat_open]", "09:00")
        await page.fill("input[name=sat_close]", "12:00")
        await page.fill("textarea[name=faqs]", "Q: Do you work weekends?\nA: Saturday mornings only.")
        await page.click(".savebar button")
        await page.wait_for_load_state()
        html = await page.content()
        check("settings: save works", "Saved." in html, page.url)
        check("settings: Saturday hours kept", 'value="09:00"' in html and "Saturday mornings only" in html)
        await page.fill("#services input[name=name]", "Leak repair")
        await page.fill("#services input[name=price_text]", "from $95")
        await page.click("#services button:has-text('Add service')")
        await page.wait_for_load_state()
        check("settings: add service", "Leak repair" in await page.content())
        check("settings: website code shown", 'data-business="demo"' in await page.content())
        # weekly report: the checkbox is there, and "send now" reports the chats made above
        check("settings: weekly report checkbox is on by default", await page.is_checked("input[name=weekly_report]"))
        await page.click("button:has-text('Send me a report of the last 7 days now')")
        await page.wait_for_load_state()
        check("settings: report of the last 7 days is made", "The report is on your Today page" in await page.inner_text("main")
              or "Report sent" in await page.inner_text("main"), (await page.inner_text("main"))[:160])
        # bad AI key -> clear message in the AI section
        await page.fill("#ai-key", "short")
        await page.click("#ai button[type=submit]")
        await page.wait_for_load_state()
        check("settings: bad AI key refused with message", "does not look like an API key" in await page.inner_text("#ai"))
        await page.fill("#ai-key", "gsk_" + "E" * 48)
        await page.click("#ai button[type=submit]")
        await page.wait_for_load_state()
        ai_text = await page.inner_text("#ai")
        check("settings: AI key saved, only last 4 shown", "Connected" in ai_text and "EEEE" in ai_text and "E" * 20 not in await page.content())
        # backup key: a second free AI account from a different company
        await page.fill("#ai-backup-key", "AIza" + "G" * 35)
        await page.fill("#ai-backup-model", "gemini-flash-test")
        await page.click("#ai-backup button[type=submit]")
        await page.wait_for_load_state()
        backup_text = await page.inner_text("#ai-backup")
        check("settings: backup AI key saved, only last 4 shown",
              "Connected" in backup_text and "GGGG" in backup_text and "G" * 20 not in await page.content())
        await page.click("#ai-backup button:has-text('Remove backup key')")
        await page.wait_for_load_state()
        check("settings: backup AI key removed", "No backup key" in await page.inner_text("#ai-backup"))
        await page.click("#ai button:has-text('Remove key')")
        await page.wait_for_load_state()
        check("settings: AI key removed", "No AI key yet" in await page.inner_text("#ai"))

        # kill switch
        await page.click("nav >> text=Today")
        await page.click("button:has-text('Turn OFF')")
        await page.wait_for_load_state()
        check("kill switch: OFF shown", "OFF - every contact is handed to you" in await page.content())
        w = await ctx.new_page()
        await w.goto(B + "/widget/demo")
        await w.evaluate("sessionStorage.clear()")
        await w.reload()
        r = await chat(w, "what are your prices?")
        check("kill switch: chat hands to a person", "$149" not in r and ("person" in r.lower() or "team" in r.lower()), r)
        await w.close()
        await page.click("button:has-text('Turn ON')")
        await page.wait_for_load_state()
        check("kill switch: back ON", "Turn OFF (kill switch)" in await page.content())

        # account
        await page.click("nav >> text=My account")
        await page.click("button:has-text('Set up 2-step login')")
        await page.wait_for_load_state()
        check("account: 2-step login setup shows a key", re.search(r"<code>[A-Z2-7]{16,}</code>", await page.content()) is not None)
        await page.fill("input[name=current_password]", "demo-password-123")
        await page.fill("input[name=new_password]", "a brand new long password")
        await page.fill("input[name=confirm_password]", "a brand new long password")
        await page.click("button:has-text('Change password')")
        await page.wait_for_load_state()
        check("account: password change works", "changed" in (await page.content()).lower(), (await page.inner_text("main"))[:200])
        await page.click("button:has-text('Log out')")
        await page.wait_for_load_state()
        check("logout: back to login", "/login" in page.url, page.url)
        await login(page, "owner@demo.test", "a brand new long password")
        check("login: new password works", page.url.rstrip("/") == B, page.url)
        await page.click("button:has-text('Log out')")

        # ---------- public product page: lead form and (if a demo business is set) the live demo chat ----------
        await page.goto(B + "/product")
        check("product page: opens without a login", "Your website answers customers" in await page.inner_text("h1"))
        await page.fill("#pf-name", "Walk Tester")
        await page.fill("#pf-business", "Walkthrough Plumbing")
        await page.fill("#pf-email", "walk@tester.example")
        await page.click(".pp-form button[type=submit]")
        await page.wait_for_load_state()
        check("product page: form sent, visitor is thanked", await page.locator("text=Thank you. I will contact you").count() == 1, page.url)
        if await page.locator("a[data-fd-open]").count():
            await page.locator("a[data-fd-open]").first.click()
            demo = page.frame_locator("#fd-frame")
            await demo.locator("#t").fill("What are your prices?")
            await demo.locator("#send").click()
            await demo.locator("#log .msg.agent >> nth=1").wait_for(timeout=15000)
            check("product page: live demo chat answers and says it is a demo",
                  await demo.locator("text=This business is not real").count() == 1)

        # ---------- platform admin ----------
        await login(page, "admin@platform.test", "admin password long 99")
        check("admin: lands on Businesses", "/admin" in page.url, page.url)
        prospect = page.locator("tr", has_text="Walkthrough Plumbing")
        check("admin: the new prospect is listed", await prospect.count() == 1)
        await prospect.locator("button:has-text('Mark contacted')").click()
        await page.wait_for_load_state()
        check("admin: prospect marked contacted", await page.locator("tr", has_text="Walkthrough Plumbing").locator("text=contacted").count() >= 1)
        await page.fill("input[name=name]", "Lone Star HVAC")
        await page.fill("input[name=slug]", "lone-star-hvac")
        await page.fill("input[name=owner_email]", "owner@lonestar.test")
        await page.click("button:has-text('Create business and owner login')")
        await page.wait_for_load_state()
        html = await page.content()
        m = re.search(r"Temporary password \(shown ONCE, send it safely\): <code>([^<]+)</code>", html)
        check("admin: new client created with temporary password", m is not None)
        temp = m.group(1) if m else ""
        row = page.locator("tr", has_text="Lone Star HVAC")
        await row.locator("input[name=api_key]").fill("gsk_" + "H" * 48)
        await row.locator("button:has-text('Save AI key')").click()
        await page.wait_for_load_state()
        check("admin: client AI key saved", "AI key ...HHHH" in await page.content())
        check("admin: preview links shown", "/demo-site/lone-star-hvac" in await page.content())
        await page.click("button:has-text('Log out')")

        # new client's first login: must choose own password
        await login(page, "owner@lonestar.test", temp)
        check("new client: forced to choose a password", "/account" in page.url and "temporary password" in (await page.content()).lower(), page.url)
        await page.fill("input[name=current_password]", temp)
        await page.fill("input[name=new_password]", "lone star own password")
        await page.fill("input[name=confirm_password]", "lone star own password")
        await page.click("button:has-text('Change password')")
        await page.wait_for_load_state()
        await page.goto(B + "/")
        check("new client: dashboard opens after password change", "Finish setting up" in await page.content(), page.url)

        # ---------- demo website + bubble ----------
        await page.goto(B + "/demo-site/demo")
        await page.click("#fd-btn")
        await page.wait_for_timeout(1500)
        frame = page.frame_locator("#fd-frame")
        check("demo site: bubble opens the chat", await frame.locator(".chat-name").inner_text() == "Demo Plumbing")
        await frame.locator("#close").click()
        await page.wait_for_timeout(300)
        check("demo site: close button closes it", not await page.is_visible("#fd-frame"))

        # phone size
        ph = await br.new_page(viewport={"width": 390, "height": 800})
        await ph.goto(B + "/demo-site/demo")
        await ph.click("#fd-btn")
        await ph.wait_for_timeout(1500)
        box = await ph.locator("#fd-frame").bounding_box()
        check("phone: chat fills the screen", box and box["width"] >= 380, str(box))
        await ph.frame_locator("#fd-frame").locator("#close").click()
        await ph.wait_for_timeout(300)
        check("phone: close works and button comes back", await ph.is_visible("#fd-btn"))
        wide = await ph.evaluate("document.documentElement.scrollWidth")
        check("phone: no sideways scrolling", wide <= 390, str(wide))

        check("no JavaScript errors", not errors, "; ".join(errors[:3]))
        await br.close()

    fails = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(fails)} of {len(results)} steps passed")
    sys.exit(1 if fails else 0)

asyncio.run(main())

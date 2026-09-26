"""Regenerate the offline HTML guide and standalone SVG flow diagrams.

Run from any directory with Python 3. Uses only the standard library. Each
workflow has a central path and labelled branches; diagrams need no JavaScript,
network connection, Mermaid renderer, or external fonts to display.
"""
from html import escape
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1] / "docs"
# Node: (ID, column, row, title, detail, category). Columns: left, main, right.
# Edge: (source, target, label, route). Routes select outside lanes for loops.
FLOWS = [
('01-message-routing', 'Message routing & access',
 'Separate your owner controls from customer conversations before any tools run.',
 'Only a verified message from Roland’s number gets owner permissions.', [
 ('in',1,0,'Incoming message','Business WhatsApp number','event'),
 ('verify',1,1,'Authentic webhook?','Validate sender and event','decision'),
 ('reject',0,1,'Reject event','No agent execution','stop'),
 ('dup',1,2,'Already processed?','Check message ID','decision'),
 ('ack',2,2,'Acknowledge only','Do not repeat actions','stop'),
 ('owner',1,3,'Is the sender Roland?','Match verified number','decision'),
 ('private',0,3,'Owner workflow','Private context and Zoho tools','owner'),
 ('intent',1,4,'Clear supported request?','Restricted customer context','decision'),
 ('refer',2,4,'Notify Roland now','Unclear or unsupported','stop'),
 ('route',1,5,'Continue customer flow','New enquiry, price list or reorder','success'),
 ], [('in','verify','',''),('verify','reject','No',''),('verify','dup','Yes',''),('dup','ack','Yes',''),('dup','owner','No',''),('owner','private','Yes',''),('owner','intent','No',''),('intent','refer','No',''),('intent','route','Yes','')]),
('02-new-customer', 'New customer enquiries',
 'Collect a name without automatically creating a Zoho client or taking an order.',
 'A missing Zoho match is not proof of a new identity. Refer uncertain matches.', [
 ('start',1,0,'New enquiry candidate','Use verified sender number','event'),
 ('known',1,1,'Existing or unclear match?','Check before onboarding','decision'),
 ('route',0,1,'Route or refer','Do not create a duplicate identity','stop'),
 ('name',1,2,'Name already supplied?','Reuse conversation details','decision'),
 ('ask',2,2,'Ask for their name','Wait for customer reply','action'),
 ('save',1,3,'Save name locally','No automatic Zoho creation','action'),
 ('thanks',1,4,'Acknowledge enquiry','No order capture in this phase','action'),
 ('summary',1,5,'Add to daily summary','Include pending name requests','success'),
 ('pdf',0,4,'Price list requested?','Run PDF flow independently','action'),
 ('order',2,4,'Wants to place an order?','Refer to Roland immediately','stop'),
 ], [('start','known','',''),('known','route','Yes',''),('known','name','No',''),('name','ask','No',''),('name','save','Yes',''),('ask','save','Name received',''),('save','thanks','',''),('thanks','summary','',''),('thanks','pdf','Prices',''),('thanks','order','Order','')]),
('03-price-list', 'Automatic price-list delivery',
 'Send the existing approved PDF from the VPS to the person who requests it.',
 'Routine successful requests go into the daily summary, not an immediate owner alert.', [
 ('request',1,0,'Customer asks for prices','New or existing customer','event'),
 ('check',1,1,'Approved PDF available?','Configured VPS path; valid PDF','decision'),
 ('missing',0,1,'Refer to Roland','Do not invent a replacement','stop'),
 ('send',1,2,'Send PDF to requester','No per-request owner approval','action'),
 ('accepted',1,3,'Request accepted?','Store provider message ID','decision'),
 ('failed',2,3,'Record failure and refer','Do not claim success','stop'),
 ('receipt',1,4,'Track delivery receipt','Accepted is not delivered','action'),
 ('journal',1,5,'Record final outcome','File version and recipient','success'),
 ('report',1,6,'Include in daily summary','Separate successes and failures','success'),
 ], [('request','check','',''),('check','missing','No',''),('check','send','Yes',''),('send','accepted','',''),('accepted','failed','No',''),('accepted','receipt','Yes',''),('receipt','journal','',''),('journal','report','','')]),
('04-reorders', 'Existing customer reorder',
 'Review the last order with current prices, obtain customer confirmation, then create a draft.',
 'The customer confirms draft creation only. They do not authorize invoice delivery.', [
 ('request',1,0,'Customer requests reorder','Identify by cellphone','event'),
 ('client',1,1,'Unique client and last order?','Never expose another client’s data','decision'),
 ('refer',0,1,'Refer to Roland','Missing or ambiguous records','stop'),
 ('price',1,2,'Products and pricing clear?','Validate availability and tax','decision'),
 ('issue',2,2,'Refer to Roland','No guessed prices or taxes','stop'),
 ('preview',1,3,'Show order proposal','Items, quantities and current total','action'),
 ('confirm',1,4,'Customer confirms?','Confirmation binds order version','decision'),
 ('change',0,4,'Revise order proposal','Recalculate and show again','action'),
 ('unclear',2,4,'Unclear response','Refer immediately','stop'),
 ('draft',1,5,'Create draft once','ZAR; due in seven days; unsent','action'),
 ('roland',1,6,'Send PDF to Roland','Customer waits for owner review','owner'),
 ], [('request','client','',''),('client','refer','No',''),('client','price','Yes',''),('price','issue','No',''),('price','preview','Yes',''),('preview','confirm','',''),('confirm','change','Changes',''),('change','preview','Revised','leftloop'),('confirm','unclear','Unclear',''),('confirm','draft','Yes',''),('draft','roland','','')]),
('05-owner-review', 'Owner review & revisions',
 'Keep the invoice unsent while Roland reviews and updates the draft.',
 'Every revision invalidates previous approval. Sending a preview to Roland never marks it sent.', [
 ('preview',1,0,'Send draft PDF to Roland','Include invoice ID and version','owner'),
 ('reply',1,1,'Roland’s response?','Verified owner conversation only','decision'),
 ('clarify',2,1,'Ask for clarification','Ambiguous invoice or instruction','stop'),
 ('update',0,2,'Apply requested changes','Update the same draft','action'),
 ('refresh',0,3,'New version and PDF','Invalidate all earlier approval','action'),
 ('approve',1,2,'Approve current version','Bind recipient and content','owner'),
 ('check',1,3,'Still the same invoice?','Recheck external Zoho edits','decision'),
 ('changed',2,3,'Content changed','New preview and approval needed','stop'),
 ('delivery',1,4,'Hand off for delivery','Only exact approved version','success'),
 ], [('preview','reply','',''),('reply','clarify','Unclear',''),('reply','update','Changes',''),('update','refresh','',''),('refresh','preview','Re-review','leftloop'),('reply','approve','Approve',''),('approve','check','',''),('check','changed','No',''),('changed','preview','Re-review','rightloop'),('check','delivery','Yes','')]),
('06-approved-delivery', 'Approved customer delivery',
 'Deliver the approved invoice, then update Zoho only after verified customer delivery.',
 'An API acceptance or a delivery receipt for Roland’s preview does not count as customer delivery.', [
 ('approved',1,0,'Owner approves version','Invoice, content and recipient bound','owner'),
 ('verify',1,1,'Approval still valid?','Verify current invoice and customer','decision'),
 ('review',0,1,'Return to owner review','Stale content or destination','stop'),
 ('send',1,2,'Send customer the PDF','Business WhatsApp conversation','action'),
 ('accepted',1,3,'Store message ID','API acceptance: still unsent in Zoho','action'),
 ('delivered',1,4,'Customer delivery verified?','Receipt matches this message','decision'),
 ('pending',2,4,'Pending or failed','Reconcile; notify Roland if failed','stop'),
 ('mark',1,5,'Mark sent in Zoho','Do not send the PDF again','action'),
 ('retry',2,5,'Zoho update failed','Retry status update only','stop'),
 ('done',1,6,'Record completion','Report outcome to Roland','success'),
 ], [('approved','verify','',''),('verify','review','No',''),('verify','send','Yes',''),('send','accepted','',''),('accepted','delivered','',''),('delivered','pending','Not yet',''),('delivered','mark','Yes',''),('mark','retry','Failure',''),('mark','done','Success','')]),
('07-referrals', 'Immediate referrals',
 'Escalate unclear or unsupported requests without waiting for the nightly summary.',
 'Pause the affected action; never invent an answer or promise Roland has read the referral.', [
 ('issue',1,0,'Unclear or unsupported request','Or a workflow needs intervention','event'),
 ('case',1,1,'Create or update referral','Customer, context and reason','action'),
 ('ack',0,2,'Acknowledge to customer','Their enquiry has been referred','action'),
 ('notify',1,2,'Notify Roland immediately','Not deferred to 20:00','owner'),
 ('receipt',1,3,'Notification delivered?','Track provider outcome','decision'),
 ('retry',2,3,'Keep referral open','Retry and surface failed notification','stop'),
 ('wait',1,4,'Await Roland’s guidance','Pause affected automation','owner'),
 ('resolve',1,5,'Resolve or resume safely','Record scoped owner instruction','success'),
 ('summary',1,6,'Include in daily summary','Even if immediate alert was sent','success'),
 ], [('issue','case','',''),('case','ack','',''),('case','notify','',''),('notify','receipt','',''),('receipt','retry','No',''),('receipt','wait','Yes',''),('wait','resolve','',''),('resolve','summary','','')]),
('08-daily-summary', 'Daily summary · 20:00 SA time',
 'Give Roland a consolidated view of enquiries, reorders, pending reviews and issues.',
 'Use Africa/Johannesburg explicitly. Immediate referrals remain a separate real-time flow.', [
 ('events',1,0,'Collect durable activity events','Not model recollection','event'),
 ('time',1,1,'20:00 South African time','Use a defined reporting cutoff','event'),
 ('exists',1,2,'Report already completed?','One logical report per cutoff','decision'),
 ('skip',0,2,'Do not duplicate','Reuse recorded outcome','stop'),
 ('build',1,3,'Build and save report','Include period activity and pending work','action'),
 ('send',1,4,'Send privately to Roland','Use permitted WhatsApp mechanism','owner'),
 ('result',1,5,'Delivery confirmed?','Persist provider message ID','decision'),
 ('retry',2,5,'Reconcile or retry','Keep the same report and period','stop'),
 ('done',1,6,'Record completion','Recover missing cutoffs after restart','success'),
 ], [('events','time','',''),('time','exists','',''),('exists','skip','Yes',''),('exists','build','No',''),('build','send','',''),('send','result','',''),('result','retry','No',''),('result','done','Yes','')]),
('09-reorder-forecast', 'Weekly reorder forecast',
 'List customers expected to order 7–14 days ahead so Roland can order stock and plan deliveries.',
 'Read-only in Zoho. Roland chooses who is included by setting cycles or excluding customers.', [
 ('time',1,0,'Monday 07:00 SA time','Weekly timer, or Roland asks Hermes','event'),
 ('saved',1,1,'Report saved this week?','Rebuild only when refresh is asked','decision'),
 ('reuse',2,1,'Return saved report','No Zoho requests','success'),
 ('read',1,2,'Read Zoho invoice history','Orders since VPS go-live; no drafts, voids or tests','action'),
 ('excluded',1,3,'Excluded by Roland?','Checked for each customer','decision'),
 ('skip',0,3,'Skip customer','Counted as excluded in the report','stop'),
 ('cycle',1,4,'Cycle set by Roland?','Otherwise use order history','decision'),
 ('owner',2,4,'Use Roland’s cycle','Last order date plus cycle days','owner'),
 ('history',1,5,'Two or more orders?','Average gap of the last 3 orders','decision'),
 ('unknown',0,5,'Unable to predict','Listed so Roland can set a cycle','stop'),
 ('when',1,6,'When is the next order?','Customers due later are not listed','decision'),
 ('overdue',0,6,'Overdue list','Expected date passed, no newer order','stop'),
 ('check',1,7,'Recheck orders used','Drop hidden test orders; predict again','action'),
 ('report',1,8,'Save the weekly report','Customers, stock, areas and value','success'),
 ('roland',1,9,'Roland plans the week','Private file, command or Hermes','owner'),
 ], [('time','saved','',''),('saved','reuse','Yes',''),('saved','read','No',''),('read','excluded','',''),('excluded','skip','Yes',''),('excluded','cycle','No',''),('cycle','owner','Yes',''),('cycle','history','No',''),('history','unknown','No',''),('history','when','Yes',''),('owner','when','','rightloop'),('when','overdue','Overdue',''),('when','check','In 7–14 days',''),('check','report','',''),('overdue','report','','leftloop'),('report','roland','','')]),
]

COLORS = {'event':('#e0f2fe','#0369a1'), 'action':('#f1f5f9','#475569'),
          'decision':('#fef3c7','#a16207'), 'owner':('#ede9fe','#7c3aed'),
          'stop':('#fff1f2','#be123c'), 'success':('#dcfce7','#15803d')}


def svg_for(slug, title, nodes, edges):
    """Return accessible standalone SVG with labelled directed paths."""
    width, box_w, box_h, stride = 1160, 286, 86, 156
    xs = [60, 437, 814]
    positions = {n[0]: (xs[n[1]], 36+n[2]*stride) for n in nodes}
    height = max(y for x,y in positions.values()) + box_h + 42
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
             f'<title id="title">{escape(title)}</title><desc id="desc">Directed workflow diagram. Follow arrows and branch labels. Decisions have amber borders; Roland actions are purple; exceptions are red.</desc>',
             '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/></marker></defs>',
             '<rect width="100%" height="100%" fill="#ffffff"/>']
    for source,target,label,route in edges:
        x,y=positions[source]; tx,ty=positions[target]
        if route:
            left = route=='leftloop'; lane=20 if left else width-20
            sx=x if left else x+box_w; ex=tx if left else tx+box_w
            points=[(sx,y+box_h/2),(lane,y+box_h/2),(lane,ty+box_h/2),(ex,ty+box_h/2)]
            lx,ly=lane+(45 if left else -45),(y+ty)/2+box_h/2
        elif x==tx:
            points=[(x+box_w/2,y+box_h),(tx+box_w/2,ty)]
            lx,ly=x+box_w/2,(y+box_h+ty)/2
        elif y==ty:
            right=tx>x
            points=[(x+box_w if right else x,y+box_h/2),(tx if right else tx+box_w,ty+box_h/2)]
            lx,ly=(points[0][0]+points[-1][0])/2,y+box_h/2-13
        else:
            right=tx>x
            sx=x+box_w if right else x; ex=tx if right else tx+box_w
            lane=(sx+ex)/2
            points=[(sx,y+box_h/2),(lane,y+box_h/2),(lane,ty+box_h/2),(ex,ty+box_h/2)]
            lx,ly=lane,(y+ty)/2+box_h/2
        coords=' '.join(f'{a},{b}' for a,b in points)
        parts.append(f'<polyline points="{coords}" fill="none" stroke="#64748b" stroke-width="2" stroke-linejoin="round" marker-end="url(#arrow)"/>')
        if label:
            lw=max(34,len(label)*7.5+12)
            parts.append(f'<rect x="{lx-lw/2}" y="{ly-12}" width="{lw}" height="24" rx="6" fill="white"/><text x="{lx}" y="{ly+4}" text-anchor="middle" font-family="Arial,sans-serif" font-size="13" font-weight="bold" fill="#334155">{escape(label)}</text>')
    for key,col,row,label,detail,category in nodes:
        x,y=positions[key]; fill,stroke=COLORS[category]
        parts.append(f'<g><title>{escape(label+": "+detail)}</title><rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="{22 if category=="decision" else 12}" fill="{fill}" stroke="{stroke}" stroke-width="{2 if category=="decision" else 1.3}"/>')
        lines=textwrap.wrap(label,30)
        top=y+25 if len(lines)>1 else y+31
        for i,line in enumerate(lines):
            parts.append(f'<text x="{x+box_w/2}" y="{top+i*19}" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" font-weight="bold" fill="#0f172a">{escape(line)}</text>')
        details=textwrap.wrap(detail,40)
        for i,line in enumerate(details):
            parts.append(f'<text x="{x+box_w/2}" y="{y+60+i*15}" text-anchor="middle" font-family="Arial,sans-serif" font-size="12" fill="#334155">{escape(line)}</text>')
        parts.append('</g>')
    parts.append('</svg>')
    return '\n'.join(parts)


def main():
    """Write nine SVGs and an offline HTML guide with navigation and zoom."""
    target=ROOT/'diagrams'; target.mkdir(exist_ok=True)
    nav=[]; cards=[]
    for index,(slug,title,intro,note,nodes,edges) in enumerate(FLOWS,1):
        svg=svg_for(slug,title,nodes,edges)
        (target/(slug+'.svg')).write_text(svg)
        nav.append(f'<a href="#{slug}"><span>{index:02}</span>{escape(title)}</a>')
        cards.append(f'''<section id="{slug}" aria-labelledby="heading-{index}">
        <div class="section-head"><div><p class="eyebrow">FLOW {index:02}</p><h2 id="heading-{index}">{escape(title)}</h2><p>{escape(intro)}</p></div><a class="prd" href="prds/{slug}.md">Read PRD ↗</a></div>
        <p class="callout">{escape(note)}</p>
        <div class="controls"><button type="button" data-zoom="out" aria-label="Zoom out diagram {index}">−</button><button type="button" data-zoom="reset">Fit</button><button type="button" data-zoom="in" aria-label="Zoom in diagram {index}">+</button><a href="diagrams/{slug}.svg" target="_blank" rel="noopener">Open SVG ↗</a><span class="zoom-status" aria-live="polite">100%</span></div>
        <div class="diagram" tabindex="0" aria-label="Scrollable diagram for {escape(title)}"><img src="diagrams/{slug}.svg" alt="{escape(title)} flow diagram" loading="lazy"/></div>
        </section>''')
    html='''<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>HustleAI · WhatsApp workflow atlas</title>
<style>
:root{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172033;background:#f3f6fa;scroll-behavior:smooth}*{box-sizing:border-box}body{margin:0}a{color:#2455ad}header{background:#11243b;color:#fff;padding:48px max(28px,calc((100vw - 1360px)/2));border-bottom:5px solid #2dd4bf}header h1{font-size:clamp(30px,4vw,48px);letter-spacing:-1.5px;margin:8px 0 12px}header p{max-width:790px;color:#d1deec;line-height:1.7}.eyebrow{font-size:12px;letter-spacing:2px;font-weight:750;color:#0f766e}.tag{display:inline-block;padding:6px 12px;background:#263b52;border:1px solid #476078;border-radius:99px;font-size:12px;color:#e0f2fe}.layout{max-width:1420px;margin:auto;display:grid;grid-template-columns:240px minmax(0,1fr);gap:28px;padding:30px 24px}aside{position:sticky;top:24px;align-self:start}aside h2{font-size:12px;text-transform:uppercase;letter-spacing:2px;color:#64748b}nav a{display:flex;gap:12px;padding:11px 9px;text-decoration:none;color:#334155;font-size:14px;border-radius:8px;line-height:1.4}nav a:hover,nav a:focus{background:#dce8f4}nav span{font-variant-numeric:tabular-nums;color:#0f766e;font-weight:700}.legend{font-size:12px;display:flex;flex-wrap:wrap;gap:8px;margin:20px 0}.legend span{padding:5px 9px;border-radius:5px;border:1px solid #cbd5e1}section{background:white;border:1px solid #dbe3ed;border-radius:18px;margin-bottom:32px;box-shadow:0 8px 28px #10233b06;scroll-margin-top:20px;overflow:hidden}.section-head{padding:26px 28px 8px;display:flex;gap:20px;justify-content:space-between;align-items:start}h2{font-size:26px;letter-spacing:-.6px;margin:6px 0 10px}.section-head p{line-height:1.6;color:#526176;max-width:690px}.section-head .eyebrow{color:#0f766e;margin:0}.prd{white-space:nowrap;font-size:13px;margin-top:12px}.callout{margin:6px 28px 18px;padding:13px 16px;border-left:3px solid #8b5cf6;background:#f5f3ff;color:#51368b;font-size:14px;line-height:1.6}.controls{display:flex;align-items:center;gap:8px;padding:0 28px 12px}.controls button{border:1px solid #cbd5e1;background:white;border-radius:6px;min-width:36px;height:32px;cursor:pointer;color:#334155}.controls button:hover{background:#e8eff6}.controls a{font-size:13px;margin-left:8px}.zoom-status{margin-left:auto;font-size:12px;color:#64748b}.diagram{overflow:auto;padding:12px 14px 24px}.diagram img{display:block;width:100%;height:auto;max-width:none}.note{font-size:13px;line-height:1.7;color:#64748b}footer{padding:24px;text-align:center;color:#64748b;font-size:13px}button:focus-visible,a:focus-visible,.diagram:focus-visible{outline:3px solid #0d9488;outline-offset:3px}@media(max-width:850px){.layout{display:block;padding:18px 12px}aside{position:static;margin-bottom:24px}nav{display:grid;grid-template-columns:1fr 1fr}.section-head{padding:20px;display:block}.callout{margin:6px 20px 16px}.diagram img{min-width:850px}.controls{padding-left:20px;padding-right:20px}header{padding:32px 22px}}@media print{header{background:white;color:#172033;padding:15px}header p{color:#475569}.layout{display:block;padding:0}aside,.controls,.prd,footer{display:none}section{break-inside:avoid;box-shadow:none;margin-bottom:20px}.diagram{overflow:visible}.diagram img{width:100%!important;min-width:0!important}h2{font-size:20px}.section-head,.callout{padding:10px;margin:0}.tag{color:#172033;background:white}*{scroll-behavior:auto}}
</style></head><body>
<header><span class="tag">Product design · Not yet deployed</span><h1>WhatsApp workflow atlas</h1><p>See how customer messages move from enquiry to invoice, where Roland steps in, and when the system may act. Nine visual flows for the Hermes + Zoho integration.</p></header>
<div class="layout"><aside><h2>Explore the flows</h2><nav>'''+''.join(nav)+'''</nav><div class="legend"><span style="background:#e0f2fe">Trigger</span><span style="background:#fef3c7">Decision</span><span style="background:#ede9fe">Roland</span><span style="background:#fff1f2">Exception</span><span style="background:#dcfce7">Outcome</span></div><p class="note">Follow the arrows from top to bottom. Branch labels explain decisions; returning arrows indicate another review.</p><p class="note">Open any SVG in its own tab for a larger view. On a phone, swipe diagrams sideways. This guide works offline.</p><p><a href="prds/README.md">Requirements index ↗</a></p></aside><main>'''+''.join(cards)+'''</main></div>
<footer>HustleAI · Agreed product flows. Open decisions and implementation dependencies remain in each PRD.</footer>
<script>
document.querySelectorAll('section').forEach(section=>{let scale=1;const img=section.querySelector('img');section.querySelectorAll('[data-zoom]').forEach(button=>button.addEventListener('click',()=>{const action=button.dataset.zoom;scale=action==='reset'?1:Math.min(2.5,Math.max(.6,scale+(action==='in'?.2:-.2)));img.style.width=(scale*100)+'%';section.querySelector('.zoom-status').textContent=Math.round(scale*100)+'%';}));});
</script></body></html>'''
    (ROOT/'flow-guide.html').write_text(html)
    print('Generated nine SVG diagrams and flow-guide.html')


if __name__=='__main__':
    main()

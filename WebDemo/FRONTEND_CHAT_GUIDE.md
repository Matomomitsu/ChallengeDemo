### Front-end guide: wire /chat for chat-content blocks (v2 standard)

This guide documents the markup and JavaScript used to wire the website cards to the `/chat` endpoint. It captures the working implementation from `chat-content1` so you can replicate it for `chat-content2` and `chat-content3` quickly.

## What the API expects/returns
- **Endpoint**: `/chat`
- **Method**: POST
- **Request body**:
```json
{ "user_input": "qual o status da minha bateria" }
```
- **Response (success)**:
```json
{ "response": "<texto da resposta>" }
```
- **Response (error)**: non-200 with `{ detail: string }` or similar.

## Markup structure (split columns)
New standard keeps the assistant content on the left and the explainer on the right above the card title. IDs should be unique per card (suffix `-2`, `-3`).

```html
<!-- Left column: assistant content -->
<section class="image-ratio_2x3 ratio_1x1_mobile-l" id="chat-content1">
  <div class="card card_body shadow_medium">
    <div class="flex_vertical gap-small">
      <div class="eyebrow">Assistente</div>
      <div id="chat-typing" class="paragraph_large" aria-live="polite"></div>
      <div id="chat-loading" class="paragraph_small text-color_secondary" style="display:none;">Consultando API…</div>
      <div id="chat-response" class="rich-text paragraph_large"></div>
      <div class="divider"></div>
    </div>
  </div>
</section>

<!-- Right column: explainer ABOVE the card title -->
<div class="header">
  <a href="#" id="toggle-explain" class="text-button w-inline-block">
    <div>Entenda como essa resposta foi gerada</div>
  </a>
  <div id="explain-panel" style="display:none;" class="margin-top_xsmall">
    <div id="explain-steps" class="flex_vertical gap-xsmall"></div>
    <div class="divider margin-top_xsmall"></div>
    <div id="explain-stats" class="flex_vertical gap-xxsmall"></div>
    <div class="margin-top_xsmall">
      <a class="button is-secondary w-button" href="https://www.semsportal.com/powerstation/PowerStatusSnMin/6ef62eb2-7959-4c49-ad0a-0ce75565023a" target="_blank" rel="noopener">Audite essa resposta</a>
    </div>
  </div>
  <h2>...</h2>
  <p class="subheading">...</p>
  <div class="button-group">
    <a href="#" id="testar-funcao-1" class="button w-button">Testar Função</a>
  </div>
</div>
```

## Elements and roles
- **assistant area (left)**: `#chat-typing`, `#chat-loading`, `#chat-response`
- **explainer area (right, above title)**: `#toggle-explain`, `#explain-panel`, `#explain-steps`, `#explain-stats`
- Keep IDs unique per card by appending `-2`, `-3` as needed
- Card 1 audit link: SEMS page; Card 2 audit link: repository URL

## Generalized JavaScript wiring
Place near the end of the page (after elements exist). Fetch starts in parallel with typing to reduce perceived latency. The backend is configured to return plain text; we also strip simple `**` if any slip through.

```html
<script>
(function(){
  function typeText(el, text, speed){
    return new Promise(function(resolve){
      var i=0; el.textContent='';
      var t=setInterval(function(){ el.textContent+=text.charAt(i++); if(i>=text.length){ clearInterval(t); resolve(); } }, speed);
    });
  }

  function renderStep(container, iconPathD, text){
    var row=document.createElement('div'); row.className='flex_horizontal gap-xxsmall';
    row.innerHTML='<div class="icon"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="'+iconPathD+'" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"></path></svg></div>'+
      '<div class="paragraph_small">'+text+'</div>';
    container.appendChild(row);
  }

  function wireChatCard(cfg){
    var typing=document.getElementById(cfg.typingId);
    var loading=document.getElementById(cfg.loadingId);
    var answer=document.getElementById(cfg.answerId);
    var toggle=document.getElementById(cfg.toggleId);
    var panel=document.getElementById(cfg.panelId);
    var steps=document.getElementById(cfg.stepsId);
    var stats=document.getElementById(cfg.statsId);
    var trigger=document.getElementById(cfg.triggerId);
    var question=cfg.questionText;
    if(!typing||!answer||!trigger) return;

    function showExplain(){ if(!panel) return; panel.style.display=panel.style.display==='none'?'block':'none'; }
    if (toggle) toggle.addEventListener('click', function(e){ e.preventDefault(); showExplain(); });

    trigger.addEventListener('click', function(e){
      e.preventDefault();
      if (steps) steps.innerHTML=''; if (stats) stats.innerHTML='';
      answer.innerHTML='';
      if (loading) loading.style.display='block';
      var t0=performance.now();
      var fetchP=fetch('/chat', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ user_input: question }) });
      typeText(typing, question, 35)
        .then(function(){ return fetchP; })
        .then(function(res){ var s=res.status; return res.json().then(function(j){ return {status:s,json:j}; }); })
        .then(function(p){
          var t1=performance.now(); var latency=Math.round(t1-t0);
          typing.textContent=''; if (loading) loading.style.display='none';
          var raw=(p.json && (p.json.response||p.json.message)) || 'Sem resposta.';
          answer.textContent=String(raw).replace(/\*\*/g,'');
          if (toggle) toggle.style.display='inline-flex';
          if (steps){
            renderStep(steps,'M5 12h14M13 5l7 7-7 7','Entrada enviada: "'+question+'"');
            renderStep(steps,'M2 12h20','Endpoint: /chat (POST)');
            renderStep(steps,'M6 12l4 4 8-8','Orquestração: call_geminiapi');
          }
          if (stats){ var n=document.createElement('div'); n.className='paragraph_small'; n.textContent='Status HTTP: '+p.status+' · Tempo de resposta: '+latency+' ms'; stats.appendChild(n); }
        })
        .catch(function(err){ typing.textContent=''; if (loading) loading.style.display='none'; answer.textContent='Erro ao consultar a API.'; console.error(err); });
    });
  }

  // Example usage for chat-content1 (status da bateria)
  document.addEventListener('DOMContentLoaded', function(){
    wireChatCard({
      typingId:'chat-typing', loadingId:'chat-loading', answerId:'chat-response',
      toggleId:'toggle-explain', panelId:'explain-panel', stepsId:'explain-steps', statsId:'explain-stats',
      triggerId:'testar-funcao-1', questionText:'qual o status da minha bateria'
    });
  });
})();
</script>
```

## Duplicating for `chat-content2` and `chat-content3`
- Duplicate the left assistant section with IDs `chat-typing-2`, `chat-loading-2`, `chat-response-2`.
- In the right column, duplicate the explainer with IDs `toggle-explain-2`, `explain-panel-2`, `explain-steps-2`, `explain-stats-2` and title/subheading/button.
- Call `wireChatCard` again with the suffixed IDs and proper question.

```html
<script>
document.addEventListener('DOMContentLoaded', function(){
  wireChatCard({
    typingId:'chat-typing-2', loadingId:'chat-loading-2', answerId:'chat-response-2',
    toggleId:'toggle-explain-2', panelId:'explain-panel-2', stepsId:'explain-steps-2', statsId:'explain-stats-2',
    triggerId:'testar-funcao-2', questionText:'otimize meu uso'
  });

  var r3=document.getElementById('chat-content3');
  var b3=document.getElementById('testar-funcao-3');
  if(r3&&b3){ wireChatCard({
    typingId:'chat-typing-3', loadingId:'chat-loading-3', answerId:'chat-response-3',
    toggleId:'toggle-explain-3', panelId:'explain-panel-3', stepsId:'explain-steps-3', statsId:'explain-stats-3',
    triggerId:'testar-funcao-3', questionText:'<pergunta do card 3>'
  }); }
});
</script>
```

## Notes
- **Explainer position**: stays in the right column above the card title to avoid sticky/scroll cutoff while keeping full content visible at 100% zoom.
- **Perceived speed**: fetch starts alongside typing; keep 30–40ms per char.
- **Output style**: backend enforces plain text; front-end strips `**` if present.
- **Metrics**: latency with `performance.now()`; status shown in explainer.
- **Audit links**: Card 1 → SEMS; Card 2 → repository (`https://github.com/Matomomitsu/ChallengeDemo`).
- **Security**: posts to `/chat` on same origin; configure CORS if using another host.

## Reference
- FlowKit framework reference for class naming and layout utilities: [Flowkit docs](https://developers.webflow.com/flowkit/getting-started/intro)




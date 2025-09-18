'use strict';

const API_CHAT_ENDPOINT = '/api/chat';
const API_EV_CHARGER_ENDPOINT = '/api/EvCharger/ChargingMode';

function typeText(element, text, speed) {
  if (!element) return Promise.resolve();
  return new Promise((resolve) => {
    let index = 0;
    element.textContent = '';
    const timer = setInterval(() => {
      element.textContent += text.charAt(index);
      index += 1;
      if (index >= text.length) {
        clearInterval(timer);
        resolve();
      }
    }, speed);
  });
}

function sanitizeResponse(raw) {
  return String(raw == null ? '' : raw).replace(/\*\*/g, '');
}

function renderStep(container, iconPathD, text) {
  if (!container) return;
  const row = document.createElement('div');
  row.className = 'flex_horizontal gap-xxsmall';
  row.innerHTML =
    '<div class="icon"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="' +
    iconPathD +
    '" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"></path></svg></div>' +
    '<div class="paragraph_small">' +
    text +
    '</div>';
  container.appendChild(row);
}

function smoothScrollTo(target, opts) {
  if (!target) return;
  let y = target.getBoundingClientRect().top + window.pageYOffset;
  if (opts && typeof opts.offset === 'number') y -= opts.offset;
  window.scrollTo({ top: y, behavior: 'smooth' });
}

function fetchChatPayload(question) {
  const startedAt = performance.now();
  return fetch(API_CHAT_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_input: question })
  })
    .then((res) =>
      res
        .json()
        .catch(() => ({ }))
        .then((json) => ({ status: res.status, json, latency: Math.round(performance.now() - startedAt) }))
    );
}

function initSmoothScroll() {
  function attach(link, target, extra) {
    if (!link || !target) return;
    link.addEventListener('click', (event) => {
      event.preventDefault();
      smoothScrollTo(target, { offset: 16 });
      if (extra) extra();
    });
  }

  document.querySelectorAll('a[href*="#"]').forEach((link) => {
    const href = link.getAttribute('href');
    if (!href) return;
    const hashIndex = href.indexOf('#');
    if (hashIndex === -1 || hashIndex === href.length - 1) return;
    const targetId = href.slice(hashIndex + 1);
    if (!targetId) return;
    const target = document.getElementById(targetId);
    attach(link, target);
  });

  const btnDescubra = document.getElementById('btn-descubra');
  const btnTeste = document.getElementById('btn-teste');
  const heroTarget = document.querySelector('.card.card_body.margin-top_large.on-secondary');
  const cliSection = document.getElementById('demo-cli-section');
  const navCliButton = document.querySelector('.nav_right .button-group a[href*="#demo-cli-section"]');

  attach(btnDescubra, heroTarget);
  attach(btnTeste, cliSection, () => {
    setTimeout(() => {
      const input = document.getElementById('cli-concept-input');
      if (input) input.focus();
    }, 400);
  });
  if (navCliButton) {
    navCliButton.addEventListener('click', () => {
      setTimeout(() => {
        const input = document.getElementById('cli-concept-input');
        if (input) input.focus();
      }, 400);
    });
  }
}

function initCard1() {
  const typingEl = document.getElementById('chat-typing');
  const responseEl = document.getElementById('chat-response');
  const loadingEl = document.getElementById('chat-loading');
  const toggleExplain = document.getElementById('toggle-explain');
  const explainPanel = document.getElementById('explain-panel');
  const explainSteps = document.getElementById('explain-steps');
  const explainStats = document.getElementById('explain-stats');
  const trigger = document.getElementById('testar-funcao-1');
  const question = 'qual o status da minha bateria';

  function toggleExplainPanel(event) {
    if (event) event.preventDefault();
    if (!explainPanel) return;
    explainPanel.style.display = explainPanel.style.display === 'none' || explainPanel.style.display === '' ? 'block' : 'none';
  }

  if (toggleExplain) {
    toggleExplain.addEventListener('click', toggleExplainPanel);
  }

  if (!trigger) return;

  trigger.addEventListener('click', (event) => {
    event.preventDefault();
    if (responseEl) responseEl.innerHTML = '';
    if (explainSteps) explainSteps.innerHTML = '';
    if (explainStats) explainStats.innerHTML = '';
    if (loadingEl) loadingEl.style.display = 'block';

    const typingPromise = typeText(typingEl, 'Usuário: ' + question, 35);
    const fetchPromise = fetchChatPayload(question);

    typingPromise
      .then(() => fetchPromise)
      .then((payload) => {
        if (loadingEl) loadingEl.style.display = 'none';
        if (typingEl) typingEl.textContent = '';
        const answer = (payload.json && (payload.json.response || payload.json.message)) || 'Sem resposta.';
        if (responseEl) {
          responseEl.textContent = 'Assistente: ' + sanitizeResponse(answer);
          if (!responseEl.classList.contains('chat-bubble')) {
            responseEl.classList.add('chat-bubble', 'chat-assistant');
          }
        }
        if (toggleExplain) toggleExplain.style.display = 'inline-flex';
        if (explainSteps) {
          renderStep(explainSteps, 'M5 12h14M13 5l7 7-7 7', 'Entrada enviada: "' + question + '"');
          renderStep(explainSteps, 'M2 12h20', 'Endpoint: /api/chat (POST)');
          renderStep(explainSteps, 'M6 12l4 4 8-8', 'Orquestração: call_geminiapi');
        }
        if (explainStats) {
          const statsRow = document.createElement('div');
          statsRow.className = 'paragraph_small';
          statsRow.textContent = 'Status HTTP: ' + payload.status + ' · Tempo de resposta: ' + payload.latency + ' ms';
          explainStats.appendChild(statsRow);
        }
      })
      .catch((err) => {
        if (loadingEl) loadingEl.style.display = 'none';
        if (typingEl) typingEl.textContent = '';
        if (responseEl) responseEl.textContent = 'Erro ao consultar a API.';
        console.error(err);
      });
  });
}

function initCard2() {
  const typing = document.getElementById('chat-typing-2');
  const loading = document.getElementById('chat-loading-2');
  const answer = document.getElementById('chat-response-2');
  const steps = document.getElementById('explain-steps-2');
  const stats = document.getElementById('explain-stats-2');
  const toggle = document.getElementById('toggle-explain-2');
  const panel = document.getElementById('explain-panel-2');
  const trigger = document.getElementById('testar-funcao-2');
  const question = 'otimize meu uso';

  function togglePanel(event) {
    if (event) event.preventDefault();
    if (!panel) return;
    panel.style.display = panel.style.display === 'none' || panel.style.display === '' ? 'block' : 'none';
  }

  if (toggle) toggle.addEventListener('click', togglePanel);
  if (!trigger) return;

  trigger.addEventListener('click', (event) => {
    event.preventDefault();
    if (steps) steps.innerHTML = '';
    if (stats) stats.innerHTML = '';
    if (answer) {
      answer.innerHTML = '';
      answer.classList.remove('chat-bubble', 'chat-assistant', 'chat-scroll', 'typography-readable');
    }
    if (loading) loading.style.display = 'block';

    const typingPromise = typeText(typing, 'Usuário: ' + question, 35);
    const fetchPromise = fetchChatPayload(question);

    typingPromise
      .then(() => fetchPromise)
      .then((payload) => {
        if (loading) loading.style.display = 'none';
        if (typing) typing.textContent = '';
        const raw = (payload.json && (payload.json.response || payload.json.message)) || 'Sem resposta.';
        if (answer) {
          answer.textContent = 'Assistente: ' + sanitizeResponse(raw);
          answer.classList.add('chat-bubble', 'chat-assistant', 'chat-scroll', 'typography-readable');
        }
        if (toggle) toggle.style.display = 'inline-flex';
        if (steps) {
          renderStep(steps, 'M5 12h14M13 5l7 7-7 7', 'Entrada do usuário enviada: "' + question + '"');
          renderStep(steps, 'M4 7h16M4 12h16M4 17h10', 'Workflow: Ativar modelo → Invocar ferramenta de otimização → Consolidar recomendações');
          renderStep(steps, 'M3 6h18', 'Extração de dados: estação Bauer (parâmetros SOC e P_meter)');
          renderStep(steps, 'M4 7h16M4 12h12', 'Normalização e análise: histórico de 7 dias com parâmetros reais minuto a minuto');
          renderStep(steps, 'M2 12h20', 'Endpoint chamado: /api/chat (POST)');
          renderStep(steps, 'M6 12l4 4 8-8', 'Orquestração: call_geminiapi + usage_optimizer → recomendações úteis');
        }
        if (stats) {
          const statsRow = document.createElement('div');
          statsRow.className = 'paragraph_small';
          statsRow.textContent = 'Status HTTP: ' + payload.status + ' · Tempo de resposta: ' + payload.latency + ' ms';
          stats.appendChild(statsRow);
        }
      })
      .catch((err) => {
        if (loading) loading.style.display = 'none';
        if (typing) typing.textContent = '';
        if (answer) answer.textContent = 'Erro ao consultar a API.';
        console.error(err);
      });
  });
}

function initCard3() {
  const typing = document.getElementById('chat-typing-3');
  const loading = document.getElementById('chat-loading-3');
  const answer = document.getElementById('chat-response-3');
  const toggle = document.getElementById('toggle-explain-3');
  const panel = document.getElementById('explain-panel-3');
  const steps = document.getElementById('explain-steps-3');
  const stats = document.getElementById('explain-stats-3');
  const trigger = document.getElementById('testar-funcao-3');
  const followUpBtn = document.getElementById('seguir-funcao-3');
  const question = 'teve algum alerta na minha rede no ultimo mês?';

  function togglePanel(event) {
    if (event) event.preventDefault();
    if (!panel) return;
    panel.style.display = panel.style.display === 'none' || panel.style.display === '' ? 'block' : 'none';
  }

  function appendBeforeDivider(element) {
    if (!element || !typing) return;
    const stack = typing.parentNode;
    if (!stack) return;
    const divider = stack.querySelector('.divider');
    if (divider) {
      stack.insertBefore(element, divider);
    } else {
      stack.appendChild(element);
    }
  }

  function handleSuccess(payload, raw) {
    if (loading) loading.style.display = 'none';
    if (typing) typing.textContent = '';
    const cleaned = sanitizeResponse(raw);
    if (answer) {
      answer.textContent = 'Assistente: ' + cleaned;
      answer.classList.add('chat-bubble', 'chat-assistant', 'chat-scroll', 'typography-readable');
    }
    if (toggle) toggle.style.display = 'inline-flex';
    if (followUpBtn) followUpBtn.style.display = 'inline-block';
    if (steps) {
      renderStep(steps, 'M5 12h14M13 5l7 7-7 7', 'Entrada enviada: "' + question + '"');
      renderStep(steps, 'M2 12h20', 'Endpoint: /api/chat (POST)');
      renderStep(steps, 'M3 6h18', 'Dados consultados: inversor da rede Bauer');
      renderStep(steps, 'M4 7h16M4 12h12', 'P_meter minuto a minuto e SOC minuto a minuto');
      renderStep(steps, 'M4 7h16M4 12h16M4 17h10', 'Análise estatística sobre médias e padrões (7 dias)');
      renderStep(steps, 'M6 12l4 4 8-8', 'Orquestração: call_geminiapi + usage_optimizer → recomendações');
    }
    if (stats) {
      const statsRow = document.createElement('div');
      statsRow.className = 'paragraph_small';
      statsRow.textContent = 'Status HTTP: ' + payload.status + ' · Tempo de resposta: ' + payload.latency + ' ms';
      stats.appendChild(statsRow);
    }
  }

  function runCard(questionText) {
    if (steps) steps.innerHTML = '';
    if (stats) stats.innerHTML = '';
    if (answer) answer.textContent = '';
    if (loading) loading.style.display = 'block';

    const typingPromise = typeText(typing, 'Usuário: ' + questionText, 35);
    const fetchPromise = fetchChatPayload(questionText);

    return typingPromise
      .then(() => fetchPromise)
      .then((payload) => {
        const raw = (payload.json && (payload.json.response || payload.json.message)) || 'Sem resposta.';
        handleSuccess(payload, raw);
        return payload;
      })
      .catch((err) => {
        if (loading) loading.style.display = 'none';
        if (typing) typing.textContent = '';
        if (answer) answer.textContent = 'Erro ao consultar a API.';
        console.error(err);
        throw err;
      });
  }

  if (toggle) toggle.addEventListener('click', togglePanel);

  if (trigger) {
    trigger.addEventListener('click', (event) => {
      event.preventDefault();
      runCard(question);
    });
  }

  if (followUpBtn) {
    followUpBtn.addEventListener('click', (event) => {
      event.preventDefault();
      const followQuestion = 'me explique melhor esse primeiro erro';
      if (loading) loading.style.display = 'block';
      const stack = typing ? typing.parentNode : null;
      if (stack) {
        const userBubble = document.createElement('div');
        userBubble.className = 'paragraph_large chat-bubble chat-user';
        userBubble.textContent = 'Usuário: ' + followQuestion;
        appendBeforeDivider(userBubble);
      }
      fetchChatPayload(followQuestion)
        .then((payload) => {
          if (loading) loading.style.display = 'none';
          const raw = (payload.json && (payload.json.response || payload.json.message)) || 'Sem resposta.';
          const cleaned = sanitizeResponse(raw);
          const assist = document.createElement('div');
          assist.className = 'paragraph_large chat-bubble chat-assistant chat-scroll typography-readable';
          assist.textContent = 'Assistente: ' + cleaned;
          appendBeforeDivider(assist);
          if (steps) {
            renderStep(steps, 'M5 12h14M13 5l7 7-7 7', 'Seguimento enviado: "' + followQuestion + '"');
          }
          if (stats) {
            const statsRow = document.createElement('div');
            statsRow.className = 'paragraph_small';
            statsRow.textContent = 'Status HTTP: ' + payload.status + ' · Tempo de resposta: ' + payload.latency + ' ms';
            stats.appendChild(statsRow);
          }
        })
        .catch((err) => {
          if (loading) loading.style.display = 'none';
          console.error(err);
        });
    });
  }
}

function initCard4() {
  const typing = document.getElementById('chat-typing-4');
  const loading = document.getElementById('chat-loading-4');
  const answer = document.getElementById('chat-response-4');
  const steps = document.getElementById('explain-steps-4');
  const stats = document.getElementById('explain-stats-4');
  const toggle = document.getElementById('toggle-explain-4');
  const panel = document.getElementById('explain-panel-4');
  const trigger = document.getElementById('testar-funcao-4');
  const modeButtons = document.querySelectorAll('.charging-mode-btn');
  let chargingMode = window.charging_mode;

  function setActiveChargingMode(mode) {
    chargingMode = mode;
    modeButtons.forEach((btn, index) => {
      if (!btn) return;
      btn.classList.toggle('active', index + 1 === mode);
    });
    window.charging_mode = mode;
  }

  function togglePanel(event) {
    if (event) event.preventDefault();
    if (!panel) return;
    panel.style.display = panel.style.display === 'none' || panel.style.display === '' ? 'block' : 'none';
  }

  function handleCard(payload, question, nextMode) {
    if (loading) loading.style.display = 'none';
    if (typing) typing.textContent = '';
    const raw = (payload.json && (payload.json.response || payload.json.message)) || 'Sem resposta.';
    if (answer) {
      answer.textContent = 'Assistente: ' + sanitizeResponse(raw);
      answer.classList.add('chat-bubble', 'chat-assistant', 'chat-scroll', 'typography-readable');
    }
    if (toggle) toggle.style.display = 'inline-flex';
    if (steps) {
      steps.innerHTML = '';
    }
    if (stats) {
      stats.innerHTML = '';
      const statsRow = document.createElement('div');
      statsRow.className = 'paragraph_small';
      statsRow.textContent = 'Status HTTP: ' + payload.status + ' · Tempo de resposta: ' + payload.latency + ' ms';
      stats.appendChild(statsRow);
    }
    setActiveChargingMode(nextMode);
  }

  function runCard() {
    if (steps) steps.innerHTML = '';
    if (stats) stats.innerHTML = '';
    if (answer) answer.innerHTML = '';
    if (loading) loading.style.display = 'block';

    let question;
    let nextMode;
    if (chargingMode === 1) {
      question = 'Altere o modo de carregamento do meu EV charger para Pv Priority';
      nextMode = 2;
    } else {
      question = 'Altere o modo de carregamento do meu EV charger para Rápido';
      nextMode = 1;
    }

    const typingPromise = typeText(typing, 'Usuário: ' + question, 35);
    const fetchPromise = fetchChatPayload(question);

    typingPromise
      .then(() => fetchPromise)
      .then((payload) => {
        handleCard(payload, question, nextMode);
      })
      .catch((err) => {
        if (loading) loading.style.display = 'none';
        if (typing) typing.textContent = '';
        if (answer) answer.textContent = 'Erro ao consultar a API.';
        console.error(err);
      });
  }

  if (toggle) toggle.addEventListener('click', togglePanel);
  if (trigger) trigger.addEventListener('click', (event) => {
    event.preventDefault();
    runCard();
  });

  fetch(API_EV_CHARGER_ENDPOINT)
    .then((res) => res.json())
    .then((data) => {
      if (typeof data.charging_mode === 'number') {
        setActiveChargingMode(data.charging_mode);
      }
    })
    .catch((err) => {
      console.error(err);
    });
}

function initFaq() {
  const toggle = document.getElementById('toggle-faq');
  const panel = document.getElementById('faq-panel');
  const label = document.getElementById('toggle-faq-label');

  if (!toggle || !panel) return;
  toggle.addEventListener('click', (event) => {
    event.preventDefault();
    const isHidden = panel.style.display === 'none' || panel.style.display === '';
    panel.style.display = isHidden ? 'block' : 'none';
    if (label) label.textContent = isHidden ? 'Ocultar perguntas' : 'Mostrar perguntas';
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initSmoothScroll();
  initCard1();
  initCard2();
  initCard3();
  initCard4();
  initFaq();
});

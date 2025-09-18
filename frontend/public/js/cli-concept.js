'use strict';

(function () {
  const endpoint = '/api/chat';
  const input = document.getElementById('cli-concept-input');
  const send = document.getElementById('cli-concept-send');
  const stack = document.getElementById('cli-concept-stack');
  const typing = document.getElementById('cli-concept-typing');
  const response = document.getElementById('cli-concept-response');
  const loading = document.getElementById('cli-concept-loading');
  const plantSelect = document.getElementById('cli-concept-plant-select');
  const plantStatus = document.getElementById('cli-concept-plant-status');
  const latencyLabel = document.getElementById('cli-concept-latency');
  const functionsLabel = document.getElementById('cli-concept-functions');
  const functionsToggle = document.getElementById('cli-concept-functions-toggle');
  const functionsPanel = document.getElementById('cli-concept-functions-panel');
  const functionsPreview = document.getElementById('cli-concept-functions-preview');
  const functionsEmpty = document.getElementById('cli-concept-functions-empty');
  const suggestions = document.getElementById('cli-concept-suggestions');
  const suggestionsToggle = document.getElementById('cli-concept-suggestions-toggle');
  const suggestionSummaries = document.querySelectorAll('.cli-concept-suggestions-summary');
  if (!input || !send || !stack) return;

  const divider = stack.querySelector('.divider');
  let turn = 0;
  let selectedPlantId = '';
  function toggleFunctionsPanel(forceState) {
    if (!functionsPanel || !functionsToggle) return;
    const current = functionsPanel.style.display === 'block';
    const next = typeof forceState === 'boolean' ? forceState : !current;
    functionsPanel.style.display = next ? 'block' : 'none';
    functionsToggle.setAttribute('aria-expanded', String(next));
    const indicator = functionsToggle.querySelector('span[aria-hidden="true"]');
    if (indicator) indicator.textContent = next ? '▴' : '▾';
  }

  if (functionsToggle) {
    functionsToggle.setAttribute('aria-expanded', 'false');
    functionsToggle.addEventListener('click', (event) => {
      event.preventDefault();
      toggleFunctionsPanel();
    });
  }

  function setFunctionsLoading(message) {
    if (!functionsEmpty || !functionsPreview) return;
    functionsPreview.innerHTML = '';
    functionsEmpty.style.display = 'block';
    functionsEmpty.textContent = message || 'Preparando integrações em tempo real…';
  }

  function updateFunctionsPreview(executed) {
    if (!functionsEmpty || !functionsPreview) return;
    functionsPreview.innerHTML = '';

    if (!executed || !executed.length) {
      functionsEmpty.style.display = 'block';
      functionsEmpty.textContent = 'Nenhuma função foi necessária nesta pergunta.';
      return;
    }

    functionsEmpty.style.display = 'none';
    executed.forEach((fn) => {
      const wrapper = document.createElement('div');
      wrapper.className = 'cli-concept-functions-item';

      const header = document.createElement('div');
      header.className = 'paragraph_small';
      header.innerHTML = `<strong>${fn.name || 'Função desconhecida'}</strong>`;
      wrapper.appendChild(header);

      const subtitle = document.createElement('div');
      subtitle.className = 'paragraph_small text-color_secondary';
      subtitle.textContent = 'Retorno antes da resposta do modelo';
      wrapper.appendChild(subtitle);

      if (fn.args && Object.keys(fn.args).length) {
        const args = document.createElement('div');
        args.className = 'paragraph_small text-color_secondary cli-concept-functions-args';
        args.textContent = `Argumentos: ${JSON.stringify(fn.args)}`;
        wrapper.appendChild(args);
      }

      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(fn.result, null, 2);
      wrapper.appendChild(pre);

      functionsPreview.appendChild(wrapper);
    });
  }

  function setPlantStatus(message) {
    if (!plantStatus) return;
    plantStatus.textContent = message || '';
  }

  function populatePlantOptions(plants) {
    if (!plantSelect) return;
    const previous = selectedPlantId;
    plantSelect.innerHTML = '';

    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = 'Usar planta padrão (Bauer)';
    plantSelect.appendChild(defaultOption);

    plants.forEach((plant) => {
      const option = document.createElement('option');
      option.value = plant.id;
      option.textContent = plant.name;
      plantSelect.appendChild(option);
    });

    if (previous && plants.some((plant) => plant.id === previous)) {
      plantSelect.value = previous;
      selectedPlantId = previous;
    } else {
      plantSelect.value = '';
      selectedPlantId = '';
    }
  }

  function getSelectedPlantLabel() {
    if (!plantSelect) return '';
    const option = plantSelect.options[plantSelect.selectedIndex];
    return option ? option.textContent : '';
  }

  function loadPlantOptions() {
    if (!plantSelect) return Promise.resolve();
    setPlantStatus('Carregando plantas disponíveis…');
    plantSelect.disabled = true;
    return fetch('/api/plants')
      .then((res) => {
        if (!res.ok) throw new Error('Failed to fetch plants');
        return res.json();
      })
      .then((data) => {
        const plants = Array.isArray(data?.plants) ? data.plants : [];
        populatePlantOptions(plants);
        if (plants.length) {
          setPlantStatus('Escolha uma planta para personalizar a análise.');
        } else {
          setPlantStatus('Nenhuma planta adicional disponível. Mantendo a padrão.');
        }
      })
      .catch(() => {
        setPlantStatus('Não foi possível carregar as plantas. Usaremos a configuração padrão.');
      })
      .finally(() => {
        plantSelect.disabled = false;
      });
  }

  function inferFunctions(text) {
    const value = (text || '').toLowerCase();
    const list = [];
    if (/(status|soc|bateria)/.test(value)) list.push('get_powerstation_battery_status');
    if (/(alerta|alarme|erro)/.test(value)) list.push('get_alarms_by_range');
    if (/(otimiz|consumo|economia)/.test(value)) list.push('usage_optimizer');
    if (/(configur|modo|charger|carregador)/.test(value)) list.push('change_ev_charger_status');
    return Array.from(new Set(list));
  }

  function sanitize(raw) {
    return String(raw == null ? '' : raw).replace(/\*\*/g, '');
  }

  function typeInto(element, prefix, text, speed) {
    if (!element) return Promise.resolve();
    const clean = sanitize(text);
    element.textContent = prefix;
    if (!clean.length) return Promise.resolve();
    let index = 0;
    return new Promise((resolve) => {
      const timer = setInterval(() => {
        index += 1;
        element.textContent = prefix + clean.slice(0, index);
        if (index >= clean.length) {
          clearInterval(timer);
          resolve();
        }
      }, speed || 24);
    });
  }

  function createAssistantBubble() {
    const bubble = document.createElement('div');
    bubble.className = 'paragraph_large chat-bubble chat-assistant typography-readable';
    if (divider) {
      stack.insertBefore(bubble, divider);
    } else {
      stack.appendChild(bubble);
    }
    return bubble;
  }

  function createUserBubble() {
    const bubble = document.createElement('div');
    bubble.className = 'paragraph_large chat-bubble chat-user';
    if (divider) {
      stack.insertBefore(bubble, divider);
    } else {
      stack.appendChild(bubble);
    }
    return bubble;
  }

  function setLoading(isLoading) {
    if (loading) loading.style.display = isLoading ? 'block' : 'none';
    send.setAttribute('aria-busy', String(isLoading));
    send.disabled = isLoading;
  }

  function updateMetrics(payload, inferred) {
    if (latencyLabel) latencyLabel.textContent = payload?.latency ? `${payload.latency} ms` : '—';
    if (functionsLabel) {
      const executed = payload?.json?.functions_preview;
      const executedNames = Array.isArray(executed) ? executed.map((item) => item.name).filter(Boolean) : [];
      const names = executedNames.length ? executedNames : inferred;
      functionsLabel.textContent = names && names.length ? names.join(', ') : '—';
    }
  }

  function submit(question) {
    const query = (question || '').trim();
    if (!query) return;

    turn += 1;
    const inferred = inferFunctions(query);

    let userBubble;
    if (turn === 1 && typing) {
      userBubble = typing;
    } else {
      userBubble = createUserBubble();
    }

    setLoading(true);
    if (latencyLabel) latencyLabel.textContent = '…';
    if (functionsLabel) {
      functionsLabel.textContent = inferred && inferred.length ? inferred.join(', ') : '—';
    }
    setFunctionsLoading();

    if (!selectedPlantId) {
      setPlantStatus('Usando a planta padrão Bauer.');
    }

    const userTyping = typeInto(userBubble, 'Usuário: ', query, 18);
    const start = performance.now();
    const requestBody = { user_input: query };
    if (selectedPlantId) requestBody.plant_id = selectedPlantId;
    const request = fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    })
      .then((res) =>
        res
          .json()
          .catch(() => ({}))
          .then((json) => ({ status: res.status, json, latency: Math.round(performance.now() - start) }))
      );

    Promise.all([userTyping, request])
      .then(([, payload]) => {
        updateFunctionsPreview(payload?.json?.functions_preview);
        const rawAnswer = sanitize(payload?.json?.response || payload?.json?.message || 'Sem resposta.');
        const answer = rawAnswer.replace(/^Assistente:\s*/i, '');
        let assistantBubble;
        if (turn === 1 && response) {
          assistantBubble = response;
        } else {
          assistantBubble = createAssistantBubble();
        }
        const fallback = Boolean(payload?.json?.fallback_to_default);
        const usedPowerstation = payload?.json?.used_powerstation_id;
        let finalAnswer = answer;
        if (fallback && plantSelect) {
          plantSelect.value = '';
          selectedPlantId = '';
        }
        if (fallback && usedPowerstation) {
          finalAnswer = answer
            ? `${answer}\n\n(Observação: não encontramos dados recentes para a planta selecionada. Voltamos para Bauer – powerstation_id ${usedPowerstation}.)`
            : `Não encontramos dados recentes para a planta selecionada. Voltamos para Bauer – powerstation_id ${usedPowerstation}.`;
        }
        if (plantStatus) {
          if (fallback) {
            setPlantStatus('Sem dados recentes para a planta escolhida. Voltamos para Bauer.');
          } else if (selectedPlantId) {
            const label = getSelectedPlantLabel() || usedPowerstation || selectedPlantId;
            setPlantStatus(`Consultando dados da planta ${label}.`);
          } else {
            setPlantStatus('Usando a planta padrão Bauer.');
          }
        }
        return typeInto(assistantBubble, 'Assistente: ', finalAnswer, 16).then(() => {
          updateMetrics(payload, inferred);
          setLoading(false);
          if (functionsPanel && functionsToggle && payload?.json?.functions_preview?.length) {
            toggleFunctionsPanel(true);
          }
        });
      })
      .catch((err) => {
        console.error(err);
        const assistantBubble = turn === 1 && response ? response : createAssistantBubble();
        assistantBubble.textContent = 'Assistente: Não consegui acessar a API no momento. Tente novamente.';
        if (latencyLabel) latencyLabel.textContent = '—';
        if (functionsLabel) functionsLabel.textContent = '—';
        setFunctionsLoading('Não foi possível carregar a pré-visualização.');
        if (plantStatus) setPlantStatus('Não foi possível concluir a consulta. Tente novamente.');
        setLoading(false);
      });
  }

  send.addEventListener('click', (event) => {
    event.preventDefault();
    submit(input.value);
    input.value = '';
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      submit(input.value);
      input.value = '';
    }
  });

  if (plantSelect) {
    plantSelect.addEventListener('change', () => {
      selectedPlantId = plantSelect.value || '';
      if (selectedPlantId) {
        const label = getSelectedPlantLabel();
        setPlantStatus(`Preparando respostas com dados da planta ${label}.`);
      } else {
        setPlantStatus('Usando a planta padrão Bauer.');
      }
    });
    loadPlantOptions().catch(() => {});
  }

  if (suggestionsToggle && suggestions) {
    suggestionsToggle.setAttribute('aria-expanded', 'false');
    suggestionsToggle.addEventListener('click', (event) => {
      event.preventDefault();
      const isOpen = suggestions.style.display === 'flex';
      suggestions.style.display = isOpen ? 'none' : 'flex';
      const indicator = suggestionsToggle.querySelector('span[aria-hidden="true"]');
      if (indicator) indicator.textContent = isOpen ? '▾' : '▴';
      suggestionsToggle.setAttribute('aria-expanded', String(!isOpen));
    });
  }

  if (suggestionSummaries && suggestionSummaries.length) {
    suggestionSummaries.forEach((summary) => {
      summary.setAttribute('aria-expanded', 'false');
      summary.addEventListener('click', (event) => {
        event.preventDefault();
        const groupId = summary.getAttribute('data-group');
        if (!groupId) return;
        const items = document.querySelector(`.cli-concept-suggestions-items[data-group-items="${groupId}"]`);
        if (!items) return;
        const isOpen = items.classList.contains('is-open');
        document.querySelectorAll('.cli-concept-suggestions-items').forEach((el) => {
          if (el !== items) el.classList.remove('is-open');
        });
        document.querySelectorAll('.cli-concept-suggestions-summary span[aria-hidden="true"]').forEach((icon) => {
          icon.textContent = '▾';
        });
        document.querySelectorAll('.cli-concept-suggestions-summary').forEach((buttonEl) => {
          if (buttonEl !== summary) buttonEl.setAttribute('aria-expanded', 'false');
        });
        items.classList.toggle('is-open', !isOpen);
        summary.querySelector('span[aria-hidden="true"]').textContent = !isOpen ? '▴' : '▾';
        summary.setAttribute('aria-expanded', String(!isOpen));
      });
    });
  }

  if (suggestions) {
    suggestions.addEventListener('click', (event) => {
      const link = event.target.closest('a[data-query]');
      if (!link) return;
      event.preventDefault();
      submit(link.getAttribute('data-query') || '');
    });
  }
})();

# Evoluindo o Frontend com Eleventy

## Arquitetura atual
- `frontend/src/pages`: páginas Eleventy (cada arquivo `.njk` gera uma rota).
- `frontend/src/partials`: componentes reutilizáveis. Use subpastas (`shared`, `sections`, `experiments`).
- `frontend/src/data`: arquivos JSON/YAML disponíveis como variáveis globais (`site`, `navigation`).
- `frontend/src/scripts`: JS que vira arquivo em `public/js`. Referencie pelo front matter `extraScripts`.
- `frontend/src/assets`: arquivos estáticos copiados para `public` (css, images, style-guide etc.).
- `frontend/src/styles`: ponto de entrada pós-processado (`main.css`).

## Fluxo seguro de trabalho
1. **Crie protótipos isolados**
   - Use `frontend/src/pages/experiments/<sua-pagina>.njk`.
   - Monte componentes em `frontend/src/partials/experiments/`.
   - Referencie CSS/JS específicos dentro do protótipo (inline ou arquivos novos).
   - Assim você valida visualmente sem mexer na landing principal.

2. **Valide sempre com `npm run build`**
   - Rode o comando dentro de `frontend/`.
   - Verifique `frontend/public/<sua-rota>/index.html` antes de integrar.

3. **Promova peças aprovadas**
   - Migre o markup para `partials/sections/`.
   - Atualize `src/pages/index.njk` para incluir a nova seção.
   - Movimente estilos para `src/styles/main.css` ou `assets/css/...`.
   - Reaproveite funções de `src/scripts/home.js` ou mova trechos comuns para módulos compartilhados.

4. **Controle de versão**
   - `frontend/public/` é gerado. Não versionar (ele volta com `npm run build`).
   - `frontend/node_modules/` também fica fora do Git.

5. **Como não quebrar nada**
   - Trabalhe em branches curtas.
   - Faça `npm run build` sempre que editar layouts/partials.
   - Confira `http://localhost:8001/demo` após rebuild (FastAPI serve essa pasta).
   - Use o painel de rede do navegador para checar 404 de assets.

## Boas referências para estudar
- **Eleventy docs**: [https://www.11ty.dev/docs/](https://www.11ty.dev/docs/) (layouts, shortcodes, collections).
- **Nunjucks** templating: [https://mozilla.github.io/nunjucks/templating.html](https://mozilla.github.io/nunjucks/templating.html).
- **Design systems**: exemplos como [https://designsystemsrepo.com/](https://designsystemsrepo.com/) mostram padrões de componentes.
- **Motion & storytelling**: estude `framer-motion`/`GSAP` demonstrações para animações suaves que você pode adaptar manualmente.
- **UX writing**: “Refactoring UI” (Larson/Schaub) e “Designing Interfaces” (Jenifer Tidwell) para inspirações visuais/coerência.

## Próximos passos sugeridos
- Revisar tipografia/escala dos cards e CLI para garantir contraste AA.
- Criar um módulo JS compartilhado para funções de digitação/fetch (evita duplicação entre `home.js` e novos protótipos).
- Preparar tokens de cores/spacing em CSS custom properties para manter tudo consistente.

/**
 * templates_lay_text.test.mjs — textos leigos e acentuados nos templates (5ª leva).
 * Regressão: páginas de engenharia/operação não devem exibir inglês cru
 * ("Health Score", "Dry run", "Timestamp") nem português sem acento
 * ("Diretorio", "Historico", "nao salvar") em texto visível ao usuário.
 * Run: node --test gateway/control/static/js/components/templates_lay_text.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const TEMPLATES = join(here, '..', '..', '..', 'templates');
const tpl = (name) => readFileSync(join(TEMPLATES, name), 'utf8');

const assess = tpl('assess.html');
const dashboard = tpl('dashboard.html');
const gateway = tpl('gateway.html');
const synthetic = tpl('synthetic.html');
const pipeline = tpl('pipeline.html');
const journeysReport = tpl('journeys-report.html');
const businessRules = tpl('business-rules.html');
const audit = tpl('audit.html');
const runNew = tpl('run_new.html');
const catalog = tpl('catalog.html');
const observability = tpl('observability.html');
const runs = tpl('runs.html');
const runDetail = tpl('run_detail.html');
const login = tpl('login.html');
const admin = tpl('admin.html');

test('assess.html: sem inglês cru nem português sem acento', () => {
  assert.match(assess, /Saúde do sistema/);
  assert.doesNotMatch(assess, /Health Score/);
  assert.match(assess, /🗑 Código sem uso/);
  assert.match(assess, /▶ Executar avaliação/);
  assert.match(assess, /Diretório do código-fonte/);
  assert.doesNotMatch(assess, /Diretorio do codigo-fonte/);
  assert.match(assess, /Recomendações/);
  assert.match(assess, /JSON inválido/);
  // severidade renderizada em pt-BR, com o código original preservado
  assert.match(assess, /high:\s*'alta'/);
  assert.doesNotMatch(assess, /toUpperCase\(\)\}<\/span>/);
});

test('dashboard.html: cartões de engenharia em pt-BR', () => {
  assert.match(dashboard, /Avaliação IA/);
  assert.doesNotMatch(dashboard, /AI Assessment/);
  assert.match(dashboard, /Código sem uso, riscos e gargalos/);
  assert.match(dashboard, /Preparação \(pipeline\)/);
  assert.match(dashboard, /Descoberta → Jornadas → Síntese/);
  assert.match(dashboard, /Analisar código-fonte/);
});

test('gateway.html: serviços e tabelas em pt-BR', () => {
  assert.match(gateway, /Sessões<\/a>/);
  assert.match(gateway, />Servidor SSH</);
  assert.doesNotMatch(gateway, /SSH Daemon/);
  assert.match(gateway, />Serviço de captura</);
  assert.doesNotMatch(gateway, /Capture Daemon \(socket\)/);
  assert.match(gateway, /Wrapper de captura<\/p>|<p[^>]*title="[^"]*registra a sessão[^"]*"[^>]*>\s*Wrapper de captura/);
  assert.match(gateway, />Data\/hora</);
  assert.doesNotMatch(gateway, />Timestamp</);
  assert.match(gateway, />Direção</);
  assert.match(gateway, /diretório de log do gateway \(log_dir\)/);
  assert.match(gateway, /sequência global de \(seq_global\)/);
});

test('synthetic.html: jornadas, estresse e verificação prévia em pt-BR', () => {
  assert.match(synthetic, /🗺️ Jornadas de negócio/);
  assert.match(synthetic, /▶ Estresse/);
  assert.match(synthetic, /📄 Relatório/);
  assert.match(synthetic, /Verificação prévia \(preflight\)/);
  assert.match(synthetic, /verificação prévia válida/);
  assert.doesNotMatch(synthetic, /preflight válido/);
  assert.match(synthetic, /• semente /);
  assert.match(synthetic, /'não'/);
  assert.doesNotMatch(synthetic, /violacao\(oes\)/);
});

test('pipeline.html: etapas e rótulos acentuados', () => {
  assert.match(pipeline, />Semente</);
  assert.match(pipeline, /Simulação — não salva \(dry run\)/);
  assert.doesNotMatch(pipeline, /nao salvar/);
  assert.match(pipeline, />Descoberta</);
  assert.match(pipeline, />Jornadas</);
  assert.match(pipeline, />Alertas</);
  assert.match(pipeline, /Diretório do código-fonte/);
  assert.match(pipeline, /Última execução/);
  assert.match(pipeline, /Concluído/);
});

test('journeys-report.html: decisões e operações em pt-BR', () => {
  assert.match(journeysReport, /Cobertura de operações \(CRUD\)/);
  assert.doesNotMatch(journeysReport, /Score CRUD/);
  assert.match(journeysReport, /<th>Criar<\/th><th>Ler<\/th><th>Atualizar<\/th><th>Apagar<\/th>/);
  assert.match(journeysReport, /Total de operações/);
  assert.match(journeysReport, /Programas inferidos e vínculos/);
  assert.match(journeysReport, /Decisão 1/);
  assert.doesNotMatch(journeysReport, /nao elegiveis/);
});

test('business-rules.html: lacunas, esboços e acentos', () => {
  assert.match(businessRules, /Lacunas de negócio detectadas/);
  assert.match(businessRules, /Esboços \(stubs\) sugeridos para criação/);
  assert.doesNotMatch(businessRules, /Stubs sugeridos para criacao/);
  assert.match(businessRules, /critical:\s*'crítica'/);
  assert.doesNotMatch(businessRules, /avaliacao de regras de negocio/);
  assert.match(businessRules, /avaliação de regras de negócio/);
});

test('audit.html: cabeçalhos e rótulos acentuados', () => {
  assert.match(audit, /<th>Pontuação<\/th>/);
  assert.match(audit, /armazenamento das entidades/);
  assert.doesNotMatch(audit, /storage das entidades/);
  assert.match(audit, /Casos: /);
  assert.match(audit, /Despacho: /);
  assert.match(audit, /Navegação — Produção/);
  assert.match(audit, /Árvore de navegação/);
  assert.doesNotMatch(audit, /diretorios/);
});

test('run_new.html: placeholders leigos com termo técnico entre parênteses', () => {
  assert.match(runNew, /placeholder="diretório de log \(log_dir\)"/);
  assert.match(runNew, /placeholder="reprocessar a partir da sequência \(seq_global\)"/);
  assert.match(runNew, /placeholder="assinatura do ponto de verificação"/);
  assert.match(runNew, /placeholder="ambiente \(env_id, opcional\)"/);
  assert.match(runNew, /placeholder="referência de credencial \(credential_ref\)"/);
  assert.match(runNew, />somente gateway</);
  assert.match(runNew, /destino somente via gateway/);
  assert.match(runNew, /<option value="off">conformidade desligada \(off\)</);
  assert.match(runNew, /<option value="strict">conformidade rigorosa \(strict\)</);
  assert.match(runNew, /captura exige início de sessão gravado \(session_start\)/);
  assert.match(runNew, /<option value="contains">por trecho \(contains\)</);
  assert.match(runNew, /<option value="fuzzy">aproximada \(fuzzy\)</);
  assert.match(runNew, /limiar de semelhança \(0 a 1\)/);
});

test('catalog.html: mesma família de traduções do run_new', () => {
  assert.match(catalog, /placeholder="diretório de log \(log_dir\)"/);
  assert.match(catalog, /placeholder="equipe responsável"/);
  assert.match(catalog, /<option value="warn">conformidade: aviso \(warn\)</);
  assert.match(catalog, /<option value="regex">expressão regular \(regex\)</);
  assert.match(catalog, /limiar de semelhança/);
  assert.match(catalog, />Políticas</);
  assert.match(catalog, />Cenários</);
});

test('observability.html: abas e filtros acentuados', () => {
  assert.match(observability, />Regressões</);
  assert.match(observability, />Tendências</);
  assert.match(observability, />Automação</);
  assert.match(observability, /diretório de log do gateway \(log_dir\)/);
  assert.match(observability, /criado em \(ms\) — de/);
  assert.match(observability, /etiquetas separadas por vírgula/);
  assert.match(observability, /<option value="private">privada</);
  assert.match(observability, /<option value="shared">compartilhada</);
});

test('runs.html: histórico, conformidade e gravidades em pt-BR', () => {
  assert.match(runs, />Histórico</);
  assert.match(runs, />Comparação</);
  assert.match(runs, /filtrar conformidade/);
  assert.match(runs, /Nova execução/);
  assert.match(runs, /placeholder="Execução #"/);
  assert.match(runs, /<option value="critical">crítica</);
  assert.match(runs, /<option value="high">alta</);
});

test('run_detail.html: execução em vez de run', () => {
  assert.match(runDetail, /Detalhe da execução/);
  assert.match(runDetail, /id da execução/);
  assert.doesNotMatch(runDetail, /Detalhe da run/);
  assert.match(runDetail, /Carregando de→para/);
});

test('login.html e admin.html: painel de controle e perfis em pt-BR', () => {
  assert.match(login, /painel de controle/);
  assert.doesNotMatch(login, /control plane/);
  assert.match(admin, /placeholder="usuário"/);
  assert.match(admin, /<option value="viewer">leitura</);
  assert.match(admin, /<option value="operator">operador</);
  assert.match(admin, /<option value="admin">administrador</);
});

test('observability.js: rótulos dinâmicos em pt-BR', () => {
  const obsJs = readFileSync(join(here, '..', 'pages', 'observability.js'), 'utf8');
  assert.match(obsJs, /etiquetas: /);
  assert.doesNotMatch(obsJs, /• tags=/);
  assert.match(obsJs, /gateway: /);
  assert.doesNotMatch(obsJs, /gateway=\$/);
  assert.match(obsJs, /tentativas: /);
  assert.match(obsJs, /execuções: /);
  assert.match(obsJs, /repetição: /);
  assert.match(obsJs, /pontuação: /);
});

test('gateway.js: pedido de log_dir em pt-BR', () => {
  const gwJs = readFileSync(join(here, '..', 'pages', 'gateway.js'), 'utf8');
  assert.doesNotMatch(gwJs, /informe um log_dir para monitorar/);
  assert.match(gwJs, /informe o diretório de log \(log_dir\) para monitorar/);
});

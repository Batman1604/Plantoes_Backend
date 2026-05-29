# Documentação Inline do Código - Plantões Backend

## Resumo das Alterações

Este documento lista as mudanças realizadas para inserir comentários explicativos em todo o código, facilitando o entendimento por futuros desenvolvedores.

## Arquivos Modificados

### 1. **Core (app/core/)**

#### config.py
- Adicionado comentário explicando a classe `Settings` como centralizadora de configurações por ambiente
- Documentação da instância global `settings`

#### database.py
- Explicação da classe `Base` para modelos SQLAlchemy
- Comentário sobre engine e SessionLocal
- Docstring na função `get_db()` que fornece sessões por requisição

#### security.py
- Docstrings para `hash_password()` - geração de hash PBKDF2-HMAC-SHA256
- Explicação de `verify_password()` - validação de senha
- Comentários em `create_purpose_token()` - tokens para email verification e password reset
- Docstring em `decode_purpose_token()` - validação com verificação de propósito
- Explicação de `create_access_token()` - tokens JWT para autenticação
- Docstring em `decode_access_token()` - decodificação com validação

#### rate_limiter.py
- Documentação de `_check()` - implementação de token bucket
- Docstring em `rate_limit()` - dependency para limitação por IP em produção

---

### 2. **Serviços (app/services/)**

#### claude_service.py
- Explicação do `SYSTEM_PROMPT` como prompt de sistema do Claude
- Comentários em `_executar_tool()` - roteamento de chamadas de ferramentas
- Docstrings em `_tool_listar_locais()`, `_tool_listar_plantoes()`, `_tool_criar_plantao()`
- Documentação de `_tool_remover_plantao()`, `_tool_atualizar_plantao()`, `_tool_marcar_executado()`
- Docstring completa em `processar_mensagem()` - processamento com tool use

#### pagamento_service.py
- Docstrings para métodos auxiliares `_ajustar_ano_mes()`, `_data_com_dia_do_mes()`, `_dia_do_ciclo_no_mes()`
- Explicação de `calcular_data_dia_util_do_mes()` - cálculo de dias úteis
- Documentação de `calcular_data_pagamento_plantao()` - lógica complexa de ciclos
- Docstring em `calcular_data_pagamento_por_dia_mes()` - simplificação mensal
- Comentário em `ajustar_fim_intervalo()` - validação de horários overnight

#### email_service.py
- Docstring em `_enviar_email()` - função interna de SMTP
- Documentação de métodos de `EmailService`: verificação, código WhatsApp, reset de senha

#### sms_service.py
- Documentação da classe `SmsService` com stub pattern
- Docstring em `_enviar_sms()` explicando implementação pendente

#### feedback_service.py
- Documentação de `enviar_feedback()` - envio para email da equipe

#### financeiro_service.py
- Docstrings em `calcular_total_mensal()`, `calcular_total_anual()`
- Explicação de `calcular_total_mensal_por_local()`

#### plano_service.py
- Documentação da classe com propósito de controle de planos
- Docstrings para `exigir_premium()`, `limitar_plano_free()`, `verificar_limite_serie()`, `upgrade_para_premium()`

---

### 3. **Repositórios (app/repositories/)**

#### plantao_repository.py
- Docstring em `UserRepository.create()` - criação com hash de senha
- Documentação de `authenticate()` - validação de credenciais
- Explicação de `list_by_user()` - listagem com exclusão de transferidos
- Docstring em `get_conflito()` - detecção de conflito de horários
- Documentação de `LocalRepository.create()` - criação de local/hospital

#### whatsapp_repository.py
- Docstring em `get_by_telefone()` - recuperação de sessão
- Documentação de `criar()` - inicialização de nova sessão
- Explicação de `atualizar_estado()`, `atualizar_historico()`, `salvar_codigo()`, `vincular_usuario()`, `resetar_sessao()`

---

### 4. **Modelos (app/models.py)**

- Comentário explicando ORM modelos como mapeamento para tabelas
- Docstring em `User` - representa usuários cadastrados
- Comentário em `Local` - local/hospital para agendamento

---

### 5. **Rotas (app/routes/)**

#### plantao_routes.py
- Docstring em `get_current_user()` - validação JWT
- Explicação de `criar_usuario()` - registro com email de verificação
- Documentação de `autenticar_usuario()` - login com JWT
- Docstring em `enviar_feedback()` - envio para equipe
- Explicação de `transferir_plantao()` - transferência via SMS

#### whatsapp_routes.py
- Comentário sobre mensagens de sistema
- Docstring em `_gerar_codigo()` - código de 6 dígitos
- Documentação de `_extrair_mensagem()` - parsing do payload Meta
- Explicação de `_responder()` - envio seguro com tratamento de erro

---

### 6. **App Principal (app/main.py)**

- Comentário sobre funções de migração leve
- Docstring em `lifespan()` - inicialização de worker
- Documentação de `notificacao_worker()` - processamento contínuo
- Explicação de `health_check()` - endpoint de saúde

---

## Padrões Aplicados

1. **Docstrings para funções públicas**: Explicam propósito e comportamento esperado
2. **Comentários em lógica complexa**: Especialmente em cálculos de pagamento e conflitos
3. **Identificação de purposes**: Cada classe/função tem seu propósito bem definido
4. **Nomes de parâmetros explicativos**: Facilitam compreensão rápida

---

## Validação

Todos os arquivos foram compilados com sucesso sem erros de sintaxe Python.

---

## Próximos Passos

Considerar adicionar:
- Exemplos de uso em docstrings de funções complexas
- Type hints mais descritivos
- Documentação de exceções esperadas
- Referências a regras de negócio em comentários estratégicos

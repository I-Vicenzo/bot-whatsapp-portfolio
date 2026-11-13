Correções da revisão pós-v1.1.0 (Fase 8). Dados continuam 100% fictícios.

### Segurança

- **Consultas isoladas por canal:** o simulador é público e aceita qualquer número. Listar, cancelar e "apagar meus dados" filtravam só pelo telefone, então quem digitasse o número de um paciente do WhatsApp podia ver, cancelar ou anonimizar as consultas dele. Agora tudo filtra também pelo canal, e o índice único "um paciente, um horário" inclui o canal (migração 0007)

### Confiabilidade

- **SQLite sem travar durante chamadas de rede:** a IA (até 15 s), o envio da resposta do atendente e os lembretes rodavam dentro da transação de escrita, e outros clientes recebiam "database is locked". Agora a chamada de rede acontece entre duas transações curtas
- **Lembretes:** recusa definitiva da Meta (template não aprovado, número inválido) não é mais reenviada a cada 5 minutos; consulta marcada a menos de 24 h não recebe lembrete logo depois da confirmação
- **LGPD:** reenvio do webhook depois de "apagar meus dados" não recria a conversa (ids externos guardados de forma opaca, migração 0008)
- **Especialidade removida** do `clinica.yaml` no meio de um agendamento: o cliente recebe aviso e menu, em vez de silêncio
- **Painel:** lista de conversas com `ORDER BY` e `LIMIT` no banco, sem agrupar a tabela inteira de mensagens a cada atualização

### Interface

- Acessibilidade: foco preservado nas atualizações do painel, contraste AA no modo escuro, foco visível, rótulos e regiões ao vivo para leitores de tela
- Simulador: texto digitado não se perde durante um envio, troca de número sem misturar históricos, mensagens de erro em português com próximo passo

### Versão

- `/health` e a documentação da API agora informam a versão certa (a v1.1.0 respondia 1.0.0)

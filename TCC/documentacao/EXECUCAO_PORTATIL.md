# Execução portátil e preservação do desenvolvimento

## Modos

| Modo | Conexão | CSV auxiliar | Relatórios |
| --- | --- | --- | --- |
| Demonstração, padrão do atalho | `.env.demonstracao` | `dados/demonstracao/cadastro_dcb_farmacia.csv` | Três XLSX fictícios em `dados/demonstracao` |
| Original, `--modo original` | `.env` | `dados/cadastro_dcb_farmacia.csv` | Arquivo real escolhido pelo usuário |

O modo é aplicado antes de importar o Flask. Todas as cinco variáveis DB_* vêm do arquivo escolhido; não há fallback automático entre os dois arquivos. O CSV é selecionado por `TCC_CADASTRO_DCB`, somente no processo iniciado. A seleção não altera os dados, a elegibilidade, as features ou o algoritmo.

Execuções manuais antigas, sem essa variável, continuam usando o CSV original. O modo original aceita PostgreSQL local ou remoto. O modo demonstrativo verifica a marca de origem e a compatibilidade exata dos 120 produtos antes de abrir a aplicação.

## Banco

O backup de `banco/demonstracao` é o mesmo já validado no TCC_BANCA: sete tabelas, com dados somente em `cadastro_produtos_farmacia`. O catálogo tem códigos, nomes, DCB e tipos reais; nenhum histórico comercial foi incluído. `tabelas_demonstracao.json` descreve colunas, chaves e relacionamentos a partir dos modelos, sem consultar o PostgreSQL.

`preparar_banco.py` é usado somente na máquina em que se deseja criar uma demonstração nova. Ele exige PostgreSQL 18.6, verifica os hashes do backup e do CSV e interrompe se houver banco, usuário ou configuração demonstrativa existentes. Nunca sobrescreve `.env`; grava somente `.env.demonstracao`. A criação futura é feita apenas após confirmação do responsável pela instalação. Não use o preparador para conectar a um banco original existente.

O inicializador verifica a conexão com SELECT/SHOW em somente leitura. Ele exige as tabelas preparadas e catálogo preenchido antes de carregar a aplicação. Os parâmetros web permitem `--porta 5001` e `--nao-abrir-navegador`; o servidor fica em 127.0.0.1, sem debug ou reloader.

## Desenvolvimento

Os arquivos antigos de `utils`, `models` e `tests` foram mantidos, incluindo limpeza inicial de CSV, inspeção e extração da farmácia, importação, diagnósticos, comparação de modelos e validação sintética. Eles representam o código existente do desenvolvimento e não precisam ser executados para utilizar `/nova-analise`.

Algumas ferramentas históricas exigem bases externas ou podem inserir dados quando executadas intencionalmente. Sua presença não as executa. Os testes de portabilidade ficam em `tests_portabilidade`, separados dos testes históricos. Não execute testes de escrita contra seu banco original.

## Cópia para entrega

`preparar_entrega.py` inclui código, testes, templates, recursos locais e dados demonstrativos. Ele exclui credenciais preenchidas, ambientes virtuais, caches, uploads, relatórios reais e o CSV cadastral completo. Não remove arquivos da pasta de trabalho, não sobrescreve o destino, não acessa bancos e não cria ZIP.

A cópia do professor usa o modo demonstrativo. Para reproduzir resultados originais, seriam necessários conexão, catálogo completo e os mesmos relatórios, fornecidos separadamente com autorização de compartilhamento.

## Verificações sem escrita

Os novos testes unitários não criam bancos:

```powershell
& ".\.venv\Scripts\python.exe" -B -m unittest tests_portabilidade.test_perfis -v
```

O relatório `VALIDACAO_PORTABILIDADE.md` registra as verificações realizadas. A preparação de um banco novo não é executada como parte desses testes unitários.

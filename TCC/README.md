# TCC — executar e avaliar o projeto

O projeto inclui o código de desenvolvimento e uma demonstração com **120 produtos reais e vendas totalmente fictícias**, em relatórios de 3, 6 e 12 meses. O Modelo C é o mesmo nos dois modos.

## 1. Requisitos

- Windows de 64 bits.
- **Python 3.14.6 de 64 bits**, disponível por `py` ou `python`.
- **PostgreSQL 18.6** funcionando, com `pg_restore` instalado.
- Internet somente para instalar as bibliotecas. A interface usa Bootstrap e Chart.js locais.

## 2. Instalar as bibliotecas

Na pasta extraída, abra **`INSTALAR.bat`** e aguarde terminar. Ele instala as versões de `requirements-lock.txt`, incluindo Flask 3.1.2.

## 3. Preparar a demonstração — uma única vez

Abra o PowerShell na pasta do projeto e execute:

```powershell
& ".\.venv\Scripts\python.exe" -B ".\preparar_banco.py"
```

Informe a porta, o administrador e a senha da instalação PostgreSQL destinada ao teste; confira e digite **SIM**. O preparador cria as tabelas, carrega o catálogo e gera **`.env.demonstracao`**. O catálogo não contém histórico de vendas reais.

Use uma instância exclusiva para demonstração. O preparador interrompe se encontrar banco `tcc_medicamentos`, usuário `tcc_banca` ou outros bancos de usuário existentes. Ele não substitui seu `.env` original. Não execute esse preparador para usar um banco original já existente.

## 4. Abrir o sistema

Abra **`INICIAR_SISTEMA.bat`**. Por padrão, ele usa `.env.demonstracao` e o CSV reduzido em `dados/demonstracao`.

O navegador abre `http://127.0.0.1:5000/nova-analise`. Selecione um dos XLSX em **`dados/demonstracao`** e clique em **Analisar**. Para encerrar, pressione **Ctrl+C** na janela do programa.

Se a porta estiver ocupada:

```powershell
.\INICIAR_SISTEMA.bat --porta 5001
```

## Usar dados originais

Mantenha a conexão original em **`.env`** e o CSV cadastral completo em **`dados/cadastro_dcb_farmacia.csv`**. O banco precisa ser PostgreSQL 18.6, chamado `tcc_medicamentos`, com as tabelas e o catálogo já preparados. A conexão pode ser local ou remota.

Abra pelo PowerShell:

```powershell
.\INICIAR_SISTEMA.bat --modo original
```

Depois, envie seu relatório real em `/nova-analise`. As vendas analisadas vêm do arquivo enviado; o banco fornece o catálogo e o CSV fornece as informações auxiliares. Não restaure o backup demonstrativo sobre o banco original.

## Preparar a cópia para o professor

A pasta de trabalho pode conter credenciais e arquivos reais. Para gerar uma cópia com código, testes e demonstração, execute:

```powershell
& ".\.venv\Scripts\python.exe" -B ".\preparar_entrega.py"
```

Será criada **`TCC_ENTREGA`**, ao lado da pasta do projeto, sem substituir uma pasta existente. Essa cópia não contém `.env`, `.env.demonstracao`, `.venv`, o CSV completo nem relatórios reais. Os arquivos originais permanecem no lugar. Nenhum ZIP é criado.

Os arquivos em `utils`, `models` e `tests` permitem avaliar o desenvolvimento. Scripts antigos de pesquisa e importação são referências e não precisam ser executados para abrir a demonstração. Detalhes e verificações estão em [documentação](documentacao/EXECUCAO_PORTATIL.md).

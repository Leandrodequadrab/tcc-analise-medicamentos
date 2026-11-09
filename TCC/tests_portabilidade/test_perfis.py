from pathlib import Path
import hashlib
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import executar_windows as executor
import preparar_banco as preparador
import preparar_entrega as entrega


class PerfisTestCase(unittest.TestCase):
    def config(self, porta):
        return f'DB_HOST=127.0.0.1\nDB_PORT={porta}\nDB_NAME=tcc_medicamentos\nDB_USER=usuario-teste\nDB_PASSWORD="senha-ficticia"\n'

    def test_perfis_usam_env_e_csv_separados_sem_sobrescrever(self):
        with tempfile.TemporaryDirectory() as pasta, patch.dict(os.environ):
            raiz=Path(pasta)
            (raiz/'.env').write_text(self.config(5432),encoding='utf-8')
            (raiz/'.env.demonstracao').write_text(self.config(55436),encoding='utf-8')
            original=(raiz/'.env').read_bytes()
            self.assertEqual(executor.ler_configuracao(raiz,'demonstracao')['DB_PORT'],'55436')
            self.assertEqual(Path(os.environ['TCC_CADASTRO_DCB']),raiz/'dados/demonstracao/cadastro_dcb_farmacia.csv')
            self.assertEqual(executor.ler_configuracao(raiz,'original')['DB_PORT'],'5432')
            self.assertEqual(Path(os.environ['TCC_CADASTRO_DCB']),raiz/'dados/cadastro_dcb_farmacia.csv')
            self.assertEqual((raiz/'.env').read_bytes(),original)

    def test_demo_nao_reaproveita_env_original_quando_falta_configuracao(self):
        with tempfile.TemporaryDirectory() as pasta, patch.dict(os.environ):
            raiz=Path(pasta);(raiz/'.env').write_text(self.config(5432))
            with self.assertRaisesRegex(executor.ErroInicializacao,'.env.demonstracao'):
                executor.ler_configuracao(raiz,'demonstracao')

    def test_original_nao_reaproveita_env_da_demonstracao(self):
        with tempfile.TemporaryDirectory() as pasta, patch.dict(os.environ):
            raiz=Path(pasta);(raiz/'.env.demonstracao').write_text(self.config(55436))
            with self.assertRaises(executor.ErroInicializacao):executor.ler_configuracao(raiz,'original')

    def test_csv_pode_ser_escolhido_sem_alterar_limpeza_ou_chave(self):
        from utils.chave_analitica import carregar_cadastro_dcb
        with tempfile.TemporaryDirectory() as pasta:
            csv=Path(pasta)/'cadastro.csv'
            csv.write_text('cod_reduzido;nom_produto;cod_dcb;nom_dcb;cod_tipo;nom_tipo\n1;PRODUTO TESTE;10;DCB TESTE;11;MED PROPAGADO OTC\n')
            with patch.dict(os.environ,{'TCC_CADASTRO_DCB':str(csv)}):
                self.assertEqual(carregar_cadastro_dcb()['nom_produto'].tolist(),['PRODUTO TESTE'])

    def test_caminho_csv_explicito_tem_prioridade(self):
        from utils.chave_analitica import carregar_cadastro_dcb
        arquivo=executor.PASTA/'dados/demonstracao/cadastro_dcb_farmacia.csv'
        with patch.dict(os.environ,{'TCC_CADASTRO_DCB':'inexistente.csv'}):
            self.assertEqual(len(carregar_cadastro_dcb(arquivo)),120)

    def test_preparador_preserva_env_original(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz=Path(pasta);(raiz/'.env').write_text('original-a-preservar')
            for rel in ('banco/demonstracao/tcc_medicamentos_demo.backup','dados/demonstracao/cadastro_dcb_farmacia.csv'):
                alvo=raiz/rel;alvo.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(executor.PASTA/rel,alvo)
            _,produtos=preparador.conferir_arquivos(raiz)
            self.assertEqual(len(produtos),120)
            cfg=preparador.Configuracao(55436,'admin','senha-admin-ficticia',Path('ferramentas'),raiz)
            preparador.salvar_configuracao(cfg,'senha-ficticia-local')
            self.assertEqual((raiz/'.env').read_text(),'original-a-preservar')
            self.assertTrue((raiz/'.env.demonstracao').is_file())
            with self.assertRaises(FileExistsError):preparador.salvar_configuracao(cfg,'outra-senha')

    def test_preparador_recusa_configuracao_demo_existente(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz=Path(pasta);(raiz/'.env.demonstracao').write_text('preservar')
            with self.assertRaisesRegex(preparador.ErroPreparacao,'.env.demonstracao'):preparador.conferir_arquivos(raiz)

    def test_preparador_bloqueia_banco_existente_antes_de_escrever(self):
        cursor=MagicMock();cursor.fetchone.side_effect=[('postgres','127.0.0.1',55436,'18.6','on'),(True,)]
        cfg=preparador.Configuracao(55436,'admin','senha-ficticia',Path('ferramentas'))
        with self.assertRaisesRegex(preparador.ErroPreparacao,'existe um banco'):preparador.conferir_destino(cursor,cfg,True)
        self.assertTrue(all(c.args[0].startswith('SELECT') for c in cursor.execute.call_args_list))

    def test_cancelar_preparacao_nao_cria_nada(self):
        cfg=preparador.Configuracao(55436,'admin','senha-ficticia',Path('ferramentas'))
        with patch.object(preparador,'validar_ambiente'),patch.object(preparador,'conferir_arquivos',return_value=(Path('backup'),[])),patch.object(preparador,'conferir_ferramentas'),patch.object(preparador,'conectar_admin') as conectar,patch.object(preparador,'conferir_destino'),patch.object(preparador,'restaurar_backup') as restaurar,patch.object(preparador,'salvar_configuracao') as salvar:
            self.assertFalse(preparador.preparar(cfg,confirmar=lambda:False,informar=lambda _:None))
            conectar.assert_called_once_with(cfg,somente_leitura=True);restaurar.assert_not_called();salvar.assert_not_called()

    def test_demo_bloqueia_catalogo_nao_demonstrativo_sem_abrir_app(self):
        import psycopg
        cursor=MagicMock();cursor.fetchone.side_effect=[('on',),('tcc_medicamentos','18.6'),*[('public.tabela',) for _ in executor.TABELAS],(1,11,'MED','PRODUTO'),(91982,0)]
        ctx=MagicMock();ctx.__enter__.return_value.cursor.return_value.__enter__.return_value=cursor
        cfg=dict(DB_HOST='127.0.0.1',DB_PORT='55436',DB_NAME='tcc_medicamentos',DB_USER='ficticio',DB_PASSWORD='ficticia')
        with patch.object(psycopg,'connect',return_value=ctx):
            with self.assertRaisesRegex(executor.ErroInicializacao,'não é o demonstrativo'):executor.verificar_postgresql(cfg)
        self.assertTrue(all(c.args[0].startswith(('SELECT','SHOW')) for c in cursor.execute.call_args_list))

    def test_entrega_exclui_credenciais_e_dados_reais(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz=Path(pasta)/'origem';raiz.mkdir()
            arquivos={'.env':'segredo','.env.demonstracao':'segredo','.env.example':'nao-distribuir','dados/cadastro_dcb_farmacia.csv':'cadastro-completo','dados/6 meses.xlsx':'vendas-reais','.venv/lib/segredo.py':'ambiente','utils/limpeza.py':'codigo','tests/test_etapa.py':'codigo','README.md':'guia','dados/demonstracao/exemplo.xlsx':'ficticio','dados/demonstracao/cadastro_dcb_farmacia.csv':'recorte','.env.original.example':'exemplo'}
            for nome,conteudo in arquivos.items():
                p=raiz/nome;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(conteudo)
            destino=Path(pasta)/'entrega'
            entrega.preparar_entrega(destino,raiz)
            copiados={p.relative_to(destino).as_posix() for p in destino.rglob('*') if p.is_file()}
            self.assertEqual(copiados,{'utils/limpeza.py','tests/test_etapa.py','README.md','dados/demonstracao/exemplo.xlsx','dados/demonstracao/cadastro_dcb_farmacia.csv','.env.original.example'})
            self.assertTrue((raiz/'.env').is_file())

    def test_entrega_nao_sobrescreve_destino_nem_copia_para_origem(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz=Path(pasta)/'origem';raiz.mkdir()
            with self.assertRaises(ValueError):entrega.preparar_entrega(raiz/'saida',raiz)
            destino=Path(pasta)/'existente';destino.mkdir()
            with self.assertRaises(ValueError):entrega.preparar_entrega(destino,raiz)


if __name__=='__main__':unittest.main()

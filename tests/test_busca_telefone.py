"""Busca de usuário por telefone (auditoria A-22).

A busca carregava TODOS os usuários com telefone cadastrado e comparava em
Python, porque o telefone não era guardado normalizado. Medido antes da
correção: 4.001 usuários carregados e 50 ms numa base de 4.000, para devolver no
máximo 5 resultados. O custo crescia com o total de usuários do app — piorava
exatamente à medida que o produto desse certo.

Agora existe a coluna indexada telefone_normalizado, mantida em sincronia por um
listener do próprio modelo.
"""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco
from app.models import User, normalizar_telefone


class ContadorDeUsuariosCarregados:
    def __init__(self):
        self.total = 0

    def __enter__(self):
        self.total = 0
        event.listen(User, "load", self._contar)
        return self

    def __exit__(self, *_):
        event.remove(User, "load", self._contar)

    def _contar(self, alvo, contexto):
        self.total += 1


class TestNormalizacao(unittest.TestCase):
    def test_formatos_equivalentes_normalizam_igual(self):
        esperado = "11999998888"
        for entrada in ("+55 (11) 99999-8888", "11999998888", "5511999998888", "11 99999 8888"):
            with self.subTest(entrada=entrada):
                self.assertEqual(normalizar_telefone(entrada), esperado)

    def test_vazio_vira_nulo(self):
        for entrada in (None, "", "   ", "---"):
            with self.subTest(entrada=entrada):
                self.assertIsNone(normalizar_telefone(entrada))


class TestBuscaPorTelefone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def criar_usuario(self, telefone=None):
        sufixo = uuid4().hex[:8]
        if telefone is None:
            telefone = f"119{uuid4().int % 100000000:08d}"
        # AUD-011: POST /api/usuarios foi removido; o usuário é gravado direto no banco.
        uid, headers = criar_usuario_no_banco(f"busca-{sufixo}@example.com", "Usuário Busca", telefone=telefone)
        return uid, headers, telefone

    def test_coluna_normalizada_e_preenchida_ao_cadastrar(self):
        uid, _, _ = self.criar_usuario(telefone="+55 (11) 91234-5678")

        with SessionLocal() as db:
            usuario = db.get(User, uid)
            self.assertEqual(usuario.telefone_normalizado, "11912345678")

    def test_coluna_normalizada_acompanha_alteracao_do_telefone(self):
        """O listener cobre UPDATE, não só INSERT.

        Hoje não existe fluxo de produto que troque o telefone de uma conta —
        PerfilUpdate aceita apenas avatar, profissão e tema, e o telefone é
        definido no cadastro. O teste exercita o invariante direto no modelo,
        para que a coluna continue correta se essa funcionalidade aparecer.
        """
        uid, _, _ = self.criar_usuario(telefone="11911112222")

        with SessionLocal() as db:
            usuario = db.get(User, uid)
            usuario.telefone = "+55 11 93333-4444"
            db.commit()

        with SessionLocal() as db:
            self.assertEqual(db.get(User, uid).telefone_normalizado, "11933334444")

    def test_encontra_com_formato_diferente_do_cadastrado(self):
        _, _, telefone = self.criar_usuario(telefone="11955556666")
        _, headers_buscador, _ = self.criar_usuario()

        # Procura com máscara e DDI, embora tenha sido cadastrado só com dígitos.
        r = self.client.get("/api/usuarios/busca?telefone=%2B55+(11)+95555-6666",
                            headers=headers_buscador)

        self.assertEqual(r.status_code, 200, r.text)
        encontrados = [u["id"] for u in r.json()["data"]]
        self.assertTrue(encontrados, "o número existe e deveria ser encontrado")

    def test_busca_nao_carrega_a_base_inteira(self):
        _, headers, _ = self.criar_usuario()

        # Uma base com vários usuários, todos com telefone.
        sufixo = uuid4().hex[:8]
        # Um hash só para os 30: com 600.000 iterações (AUD-059), 30 hashes
        # somavam ~12 s a este teste, que mede consultas, não hash.
        senha_hash = hash_password("SenhaForte123")
        with SessionLocal() as db:
            db.add_all([
                User(nome=f"Massa {i}", email=f"massa-{sufixo}-{i}@example.com",
                     telefone=f"1197{i:07d}", senha_hash=senha_hash)
                for i in range(30)
            ])
            db.commit()

        with ContadorDeUsuariosCarregados() as contador:
            r = self.client.get("/api/usuarios/busca?telefone=11970000005", headers=headers)

        self.assertEqual(r.status_code, 200, r.text)
        # O buscador autenticado conta como um carregamento; o que não pode é a
        # base inteira vir junto.
        self.assertLess(
            contador.total,
            10,
            f"a busca carregou {contador.total} usuários — se cresceu com a base, "
            "a comparação voltou a ser feita em Python",
        )


if __name__ == "__main__":
    unittest.main()

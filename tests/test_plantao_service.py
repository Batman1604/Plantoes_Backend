"""A-13 (4/4): PlantaoService e a dependência do SyncService.

O AUD-001 aconteceu porque o SyncService chamava o handler `deletar_plantao`
como função Python e o default `Query("somente_este")` (um objeto, não a
string) caía no ramo "este e futuros". Estes testes amarram as duas metades da
correção estrutural:

1. `sync_service.py` não importa nada de `app.routes`;
2. `PlantaoService` recusa `aplicar_em` fora dos dois valores aceitos em vez de
   tratá-lo como o caso mais destrutivo.
"""

import ast
import unittest
from datetime import date, time
from pathlib import Path
from uuid import uuid4

from fastapi import Query

from app.core.database import SessionLocal
from app.models import Local, Plantao, User
from app.services.plantao_service import PlantaoService


def _usuario_com_tres_avulsos(db):
    usuario = User(nome="Service", email=f"svc-{uuid4().hex[:8]}@example.com")
    db.add(usuario)
    db.flush()
    local = Local(usuario_id=usuario.id, nome_local="Hospital", data_inicio=date(2026, 1, 1), data_final=date(2026, 1, 31))
    db.add(local)
    db.flush()
    plantoes = []
    for dia in (17, 24, 31):
        p = Plantao(
            usuario_id=usuario.id, local_id=local.id, valor=100, data_inicio=date(2026, 7, dia),
            data_fim=date(2026, 7, dia), horario_inicio=time(7), horario_fim=time(19),
        )
        db.add(p)
        plantoes.append(p)
    db.commit()
    return usuario, plantoes


class TestSyncNaoDependeDasRotas(unittest.TestCase):
    def test_sync_service_nao_importa_app_routes(self):
        arquivo = Path(__file__).resolve().parent.parent / "app" / "services" / "sync_service.py"
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        modulos = [
            n.module for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom) and n.module
        ] + [
            alias.name for n in ast.walk(arvore) if isinstance(n, ast.Import) for alias in n.names
        ]
        self.assertEqual([m for m in modulos if m.startswith("app.routes")], [])


class TestAplicarEmExplicito(unittest.TestCase):
    def test_deletar_com_aplicar_em_invalido_falha_alto_e_nao_apaga_nada(self):
        with SessionLocal() as db:
            usuario, plantoes = _usuario_com_tres_avulsos(db)
            ids = [p.id for p in plantoes]
            with self.assertRaises(ValueError):
                PlantaoService.deletar(db, usuario, ids[0], aplicar_em=Query("somente_este"))
            restantes = db.query(Plantao.id).filter(Plantao.usuario_id == usuario.id).count()
        self.assertEqual(restantes, 3)

    def test_deletar_somente_este_apaga_um(self):
        with SessionLocal() as db:
            usuario, plantoes = _usuario_com_tres_avulsos(db)
            ids = [p.id for p in plantoes]
            total = PlantaoService.deletar(db, usuario, ids[0], aplicar_em="somente_este")
            restantes = sorted(i for (i,) in db.query(Plantao.id).filter(Plantao.usuario_id == usuario.id))
        self.assertEqual((total, restantes), (1, ids[1:]))


if __name__ == "__main__":
    unittest.main()

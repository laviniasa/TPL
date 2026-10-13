# atualizar_programacao_tpl.py
"""
Atualiza a programação semanal do TPL usando a tabela definida abaixo.

Segurança:
- Faz backup JSON da programação semanal antes de alterar.
- Não apaga reservas.
- Não altera reservas existentes automaticamente.
- Não altera programações de datas passadas.
- Sincroniza programações futuras com a nova regra semanal.
- Horários antigos que tenham reservas/inscrições são preservados e listados
  para revisão manual; horários antigos sem vínculos podem ser removidos.
- Faz validações antes de qualquer alteração.
Execute na pasta raiz do projeto (a pasta que contém run.py).
"""

from collections import Counter
from datetime import date, datetime, timedelta
import json
import re
import unicodedata
from pathlib import Path

from app import create_app
from app.models import (
    db,
    ProgramacaoSemanal,
    Programacao,
    Reserva,
    Inscricao,
    Carrinho,
    Local,
)

# Índices: segunda=0 ... domingo=6.
PROGRAMACAO = {
    0: {
        "dia": "Segunda-feira",
        "locais": ["Praça (Centro)", "Rodoviária", "Parque Bergamasco"],
        "horarios": [
            ("07:00", "08:00"), ("08:00", "10:00"), ("10:00", "11:00"),
            ("14:00", "15:00"), ("15:00", "16:00"), ("16:00", "17:00"),
            ("17:00", "18:00"), ("18:00", "19:00"),
        ],
    },
    1: {
        "dia": "Terça-feira",
        "locais": ["Rodoviária", "Praça (Centro)", "Parque Bergamasco"],
        "horarios": [
            ("06:00", "08:00"), ("08:00", "10:00"), ("10:00", "11:00"),
            ("14:00", "15:00"), ("15:00", "16:00"), ("16:00", "17:00"),
            ("18:00", "19:00"), ("19:00", "20:00"),
        ],
    },
    2: {
        "dia": "Quarta-feira",
        "locais": ["Rodoviária", "Parque Bergamasco", "Parque St. Maria"],
        "horarios": [
            ("07:00", "08:00"), ("08:00", "09:00"), ("09:00", "10:00"),
            ("10:00", "11:00"), ("14:00", "15:00"), ("15:00", "16:00"),
            ("16:00", "17:00"), ("17:00", "18:00"), ("18:00", "19:00"),
            ("19:00", "20:00"),
        ],
    },
    3: {
        "dia": "Quinta-feira",
        "locais": ["Praça (Centro)", "Rodoviária", "Parque Bergamasco"],
        "horarios": [
            ("06:00", "07:00"), ("07:00", "08:00"), ("08:00", "09:00"),
            ("09:00", "10:00"), ("10:00", "11:00"), ("14:00", "15:00"),
            ("15:00", "16:00"), ("16:00", "18:00"), ("18:00", "19:00"),
        ],
    },
    4: {
        "dia": "Sexta-feira",
        "locais": ["Rodoviária", "Praça Centro", "Rodoviária"],
        "horarios": [
            ("07:00", "08:00"), ("08:00", "09:00"), ("09:00", "10:00"),
            ("10:00", "11:00"), ("15:00", "17:00"), ("18:00", "20:00"),
        ],
    },
    5: {
        "dia": "Sábado",
        "locais": ["Praça (Centro)", "Feira/Praça", "Rodoviária"],
        "horarios": [("07:00", "09:00"), ("09:00", "11:00"), ("11:00", "14:00")],
    },
    6: {
        "dia": "Domingo",
        "locais": ["Rodoviária", "Bar da Estação", "Parque Bergamasco"],
        "horarios": [
            ("08:00", "10:00"), ("10:00", "11:00"), ("14:00", "16:00"),
            ("16:00", "17:00"), ("18:00", "19:30"),
        ],
    },
}

# IDs observados no banco informado pelo usuário. Usados como verificação
# explícita para evitar associar "Praça Centro" a "Praça (Centro)" por engano.
IDS_LOCAIS_ESPERADOS = {
    "Rodoviária": 2,
    "Praça (Centro)": 5,
    "Parque Bergamasco": 6,
    "Parque St. Maria": 7,
    "Praça Centro": 8,
    "Feira/Praça": 9,
    "Bar da Estação": 10,
}


def normalizar(valor):
    valor = unicodedata.normalize("NFKD", str(valor or ""))
    valor = "".join(c for c in valor if not unicodedata.combining(c))
    valor = valor.lower()
    valor = re.sub(r"[^a-z0-9]+", " ", valor)
    return " ".join(valor.split())


def encontrar_local_exato(nome, locais_por_id):
    """Resolve apenas por nome exato; aliases ambíguos não são aceitos."""
    candidatos = [
        local for local in locais_por_id.values()
        if local.nome.strip().casefold() == nome.strip().casefold()
        and local.ativo
    ]
    if len(candidatos) != 1:
        return None

    local = candidatos[0]
    id_esperado = IDS_LOCAIS_ESPERADOS.get(nome)
    if id_esperado is not None and local.id != id_esperado:
        raise RuntimeError(
            f"O local '{nome}' está no ID {local.id}, mas o ID esperado "
            f"pelo cadastro verificado era {id_esperado}. Nenhuma alteração feita."
        )
    return local


def tem_reserva_no_horario(data_programacao, hora_inicio, hora_fim, carrinho_id):
    """Replica a regra do routes.py para reservas ativas que cruzam o intervalo."""
    return Reserva.query.filter(
        Reserva.ativo.is_(True),
        Reserva.hora_inicio < hora_fim,
        Reserva.hora_fim > hora_inicio,
        db.or_(
            Reserva.data_inicio == data_programacao,
            db.and_(
                Reserva.tipo == "FIXA",
                Reserva.dia_semana == data_programacao.weekday(),
                Reserva.data_inicio <= data_programacao,
            ),
        ),
        Reserva.carrinho_id == carrinho_id,
    ).first() is not None


def tem_inscricao(programacao_id):
    return Inscricao.query.filter_by(programacao_id=programacao_id).first() is not None


def serializar_programacao_semanal(registros):
    return [
        {
            "id": p.id,
            "dia_semana": p.dia_semana,
            "hora_inicio": p.hora_inicio.strftime("%H:%M"),
            "hora_fim": p.hora_fim.strftime("%H:%M"),
            "carrinho_id": p.carrinho_id,
            "local_id": p.local_id,
            "quantidade_maxima": p.quantidade_maxima,
        }
        for p in registros
    ]


def main():
    app = create_app()

    with app.app_context():
        carrinhos = (
            Carrinho.query.filter_by(ativo=True).order_by(Carrinho.id).all()
        )
        if len(carrinhos) != 3:
            raise SystemExit(
                f"Esperava exatamente 3 carrinhos ativos; encontrei {len(carrinhos)}. "
                "Nenhuma alteração feita."
            )

        locais = Local.query.filter_by(ativo=True).order_by(Local.id).all()
        locais_por_id = {local.id: local for local in locais}

        # Resolver e validar todos os locais antes de mexer no banco.
        locais_resolvidos = {}
        faltantes = set()
        for config in PROGRAMACAO.values():
            for nome in config["locais"]:
                if nome not in locais_resolvidos:
                    local = encontrar_local_exato(nome, locais_por_id)
                    if local is None:
                        faltantes.add(nome)
                    else:
                        locais_resolvidos[nome] = local

        if faltantes:
            print("Não encontrei os seguintes locais ativos com nome EXATO:")
            for nome in sorted(faltantes):
                print(f" - {nome}")
            raise SystemExit(
                "Nenhuma alteração foi feita. Corrija os nomes na administração do TPL "
                "ou ajuste PROGRAMACAO para usar os nomes reais."
            )

        existentes_semanais = ProgramacaoSemanal.query.all()
        if not existentes_semanais:
            raise SystemExit(
                "Não encontrei programação semanal atual para obter a capacidade "
                "dos carrinhos. Cadastre uma programação inicial ou defina as capacidades "
                "manualmente no script. Nenhuma alteração feita."
            )

        capacidade_por_carrinho = {}
        for carrinho in carrinhos:
            capacidades = [
                p.quantidade_maxima
                for p in existentes_semanais
                if p.carrinho_id == carrinho.id and p.quantidade_maxima is not None
            ]
            if not capacidades:
                raise SystemExit(
                    f"Não consegui identificar a capacidade do {carrinho.nome}. "
                    "Nenhuma alteração feita."
                )
            capacidade_por_carrinho[carrinho.id] = Counter(capacidades).most_common(1)[0][0]

        novas_linhas = []
        for dia_numero, config in PROGRAMACAO.items():
            for inicio_txt, fim_txt in config["horarios"]:
                inicio = datetime.strptime(inicio_txt, "%H:%M").time()
                fim = datetime.strptime(fim_txt, "%H:%M").time()
                for indice, carrinho in enumerate(carrinhos):
                    nome_local = config["locais"][indice]
                    novas_linhas.append({
                        "dia_semana": dia_numero,
                        "hora_inicio": inicio,
                        "hora_fim": fim,
                        "carrinho_id": carrinho.id,
                        "local_id": locais_resolvidos[nome_local].id,
                        "quantidade_maxima": capacidade_por_carrinho[carrinho.id],
                        "dia_nome": config["dia"],
                        "carrinho_nome": carrinho.nome,
                        "local_nome": nome_local,
                    })

        # Validação de sobreposição por carrinho/dia antes de alterar.
        por_dia_carrinho = {}
        for linha in novas_linhas:
            chave = (linha["dia_semana"], linha["carrinho_id"])
            por_dia_carrinho.setdefault(chave, []).append(linha)
        for (dia, carrinho_id), linhas in por_dia_carrinho.items():
            linhas.sort(key=lambda x: x["hora_inicio"])
            for anterior, atual in zip(linhas, linhas[1:]):
                if anterior["hora_fim"] > atual["hora_inicio"]:
                    raise SystemExit(
                        f"Há sobreposição na configuração de {PROGRAMACAO[dia]['dia']}, "
                        f"carrinho ID {carrinho_id}: "
                        f"{anterior['hora_inicio']}–{anterior['hora_fim']} e "
                        f"{atual['hora_inicio']}–{atual['hora_fim']}. Nenhuma alteração feita."
                    )

        hoje = date.today()
        # Usar o mesmo fuso horário do aplicativo.
        try:
            from zoneinfo import ZoneInfo
            hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        except Exception:
            pass

        # Planeja como sincronizar datas futuras antes de confirmar.
        novas_por_chave = {
            (x["dia_semana"], x["carrinho_id"], x["hora_inicio"], x["hora_fim"]): x
            for x in novas_linhas
        }
        programacoes_futuras = Programacao.query.filter(Programacao.data >= hoje).all()

        # Para cada data futura, as novas faixas serão criadas; faixas antigas
        # sem reserva/inscrição poderão ser removidas; as vinculadas são preservadas.
        plano_criar = []
        plano_atualizar = []
        plano_remover = []
        plano_preservar = []

        por_data_carrinho_horario = {
            (p.data, p.carrinho_id, p.hora_inicio, p.hora_fim): p
            for p in programacoes_futuras
        }

        datas_relevantes = {p.data for p in programacoes_futuras}
        # Também prepara os próximos 90 dias, para que a nova agenda já esteja pronta.
        for offset in range(90):
            datas_relevantes.add(hoje + timedelta(days=offset))

        for data_programacao in sorted(datas_relevantes):
            dia = data_programacao.weekday()
            linhas_dia = [
                x for x in novas_linhas if x["dia_semana"] == dia
            ]
            chaves_validas = set()
            existentes_data = [
                p for p in programacoes_futuras if p.data == data_programacao
            ]

            for linha in linhas_dia:
                chave = (
                    data_programacao, linha["carrinho_id"],
                    linha["hora_inicio"], linha["hora_fim"],
                )
                chaves_validas.add(chave)
                atual = por_data_carrinho_horario.get(chave)
                if atual is None:
                    plano_criar.append((data_programacao, linha))
                elif (
                    atual.local_id != linha["local_id"]
                    or atual.quantidade_maxima != linha["quantidade_maxima"]
                ):
                    plano_atualizar.append((atual, linha))

            for atual in existentes_data:
                chave = (
                    atual.data, atual.carrinho_id,
                    atual.hora_inicio, atual.hora_fim,
                )
                if chave in chaves_validas:
                    continue
                if (
                    tem_reserva_no_horario(
                        atual.data, atual.hora_inicio, atual.hora_fim, atual.carrinho_id
                    )
                    or tem_inscricao(atual.id)
                ):
                    plano_preservar.append(atual)
                else:
                    plano_remover.append(atual)

        print("=== PRÉVIA DA ATUALIZAÇÃO ===")
        print("Carrinhos ativos:", ", ".join(c.nome for c in carrinhos))
        print("Locais resolvidos:")
        for nome, local in sorted(locais_resolvidos.items()):
            print(f" - {nome}: ID {local.id} ({local.nome})")
        print(f"Novos registros semanais: {len(novas_linhas)}")
        print(f"Programações futuras a criar: {len(plano_criar)}")
        print(f"Programações futuras a atualizar (local/capacidade): {len(plano_atualizar)}")
        print(f"Programações antigas sem vínculos a remover: {len(plano_remover)}")
        print(f"Programações antigas com reserva/inscrição a preservar: {len(plano_preservar)}")
        if plano_preservar:
            print("\nHorários preservados para revisão manual:")
            for p in plano_preservar[:50]:
                print(
                    f" - {p.data:%d/%m/%Y}, carrinho {p.carrinho_id}, "
                    f"{p.hora_inicio:%H:%M}-{p.hora_fim:%H:%M}, "
                    f"programação ID {p.id}"
                )
            if len(plano_preservar) > 50:
                print(f" ... e mais {len(plano_preservar) - 50} registros.")

        print("\nATENÇÃO: reservas existentes não serão alteradas automaticamente.")
        confirmacao = input("Para aplicar as alterações, digite APLICAR: ").strip()
        if confirmacao != "APLICAR":
            raise SystemExit("Cancelado. Nenhuma alteração foi feita.")

        backup_path = Path.cwd() / (
            f"backup_programacao_tpl_{datetime.now():%Y%m%d_%H%M%S}.json"
        )
        backup = {
            "criado_em": datetime.now().isoformat(timespec="seconds"),
            "programacao_semanal": serializar_programacao_semanal(existentes_semanais),
            "programacao_futura": [
                {
                    "id": p.id,
                    "data": p.data.isoformat(),
                    "hora_inicio": p.hora_inicio.strftime("%H:%M"),
                    "hora_fim": p.hora_fim.strftime("%H:%M"),
                    "carrinho_id": p.carrinho_id,
                    "local_id": p.local_id,
                    "quantidade_maxima": p.quantidade_maxima,
                }
                for p in programacoes_futuras
            ],
        }
        backup_path.write_text(
            json.dumps(backup, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        try:
            # Substitui a programação semanal numa transação.
            ProgramacaoSemanal.query.delete(synchronize_session=False)
            for linha in novas_linhas:
                db.session.add(ProgramacaoSemanal(
                    dia_semana=linha["dia_semana"],
                    hora_inicio=linha["hora_inicio"],
                    hora_fim=linha["hora_fim"],
                    carrinho_id=linha["carrinho_id"],
                    local_id=linha["local_id"],
                    quantidade_maxima=linha["quantidade_maxima"],
                ))
            db.session.flush()

            # Atualiza registros futuros correspondentes, cria os novos e remove
            # apenas horários obsoletos que não tenham reservas/inscrições.
            for programacao, linha in plano_atualizar:
                programacao.local_id = linha["local_id"]
                programacao.quantidade_maxima = linha["quantidade_maxima"]

            for data_programacao, linha in plano_criar:
                # Reconfere para evitar duplicidade caso o plano contenha chaves iguais.
                duplicado = Programacao.query.filter_by(
                    data=data_programacao,
                    carrinho_id=linha["carrinho_id"],
                    hora_inicio=linha["hora_inicio"],
                    hora_fim=linha["hora_fim"],
                ).first()
                if duplicado:
                    duplicado.local_id = linha["local_id"]
                    duplicado.quantidade_maxima = linha["quantidade_maxima"]
                else:
                    db.session.add(Programacao(
                        data=data_programacao,
                        hora_inicio=linha["hora_inicio"],
                        hora_fim=linha["hora_fim"],
                        carrinho_id=linha["carrinho_id"],
                        local_id=linha["local_id"],
                        quantidade_maxima=linha["quantidade_maxima"],
                    ))

            for programacao in plano_remover:
                db.session.delete(programacao)

            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        print("\nAtualização concluída com sucesso.")
        print(f"Backup salvo em: {backup_path}")
        print("As reservas existentes não foram alteradas.")
        print("Confira a agenda no sistema antes de divulgar a nova programação.")
        if plano_preservar:
            print(
                "Importante: existem horários antigos com reserva/inscrição. "
                "Eles foram mantidos e precisam de revisão manual."
            )


if __name__ == "__main__":
    main()

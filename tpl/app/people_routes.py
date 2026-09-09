from flask import Blueprint, render_template, request, redirect, url_for

from app.models import db, Pessoa


people = Blueprint("people", __name__, url_prefix="/admin/pessoas")


@people.route("/")
def pessoas():
    pessoas_lista = Pessoa.query.order_by(Pessoa.ativo.desc(), Pessoa.nome).all()

    return render_template(
        "admin_pessoas.html",
        pessoas=pessoas_lista
    )


@people.route("/adicionar", methods=["POST"])
def adicionar():
    nome = request.form.get("nome", "").strip()

    if not nome:
        return redirect(url_for("people.pessoas"))

    existente = Pessoa.query.filter_by(nome=nome).first()

    if existente:
        if not existente.ativo:
            existente.ativo = True
            db.session.commit()
        return redirect(url_for("people.pessoas"))

    pessoa = Pessoa(nome=nome, ativo=True)
    db.session.add(pessoa)
    db.session.commit()

    return redirect(url_for("people.pessoas"))


@people.route("/editar/<int:pessoa_id>", methods=["POST"])
def editar(pessoa_id):
    pessoa = Pessoa.query.get_or_404(pessoa_id)
    nome = request.form.get("nome", "").strip()

    if not nome:
        return redirect(url_for("people.pessoas"))

    outra = Pessoa.query.filter(
        Pessoa.nome == nome,
        Pessoa.id != pessoa.id
    ).first()

    if outra:
        return redirect(url_for("people.pessoas"))

    pessoa.nome = nome
    db.session.commit()

    return redirect(url_for("people.pessoas"))


@people.route("/alternar/<int:pessoa_id>", methods=["POST"])
def alternar(pessoa_id):
    pessoa = Pessoa.query.get_or_404(pessoa_id)
    pessoa.ativo = not pessoa.ativo
    db.session.commit()

    return redirect(url_for("people.pessoas"))

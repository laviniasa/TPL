# TPL — Testemunho Público Local

Aplicação web responsiva desenvolvida em **Python e Flask** para organizar a programação do Testemunho Público Local (TPL) da congregação.

## Sobre o projeto

O sistema foi pensado para facilitar a organização dos horários de testemunho público, permitindo consultar a agenda e reservar horários de acordo com os carrinhos, locais e participantes disponíveis.

## Principais recursos

- Visualização da agenda por dia e horário
- Organização de até 3 carrinhos
- Cadastro de locais de testemunho
- Reserva de horários
- Seleção do irmão responsável e dos acompanhantes
- Validação para evitar conflitos de horário entre participantes
- Exclusão de reservas
- Área administrativa para gerenciamento de pessoas e, futuramente, programação, carrinhos, locais e reservas
- Interface responsiva para uso em computador e celular

## Tecnologias

- **Python**
- **Flask**
- **Flask-SQLAlchemy**
- **SQLite**
- **HTML / CSS / JavaScript**

## Estrutura do projeto

```text
tpl/
├── app/
│   ├── templates/
│   ├── static/
│   ├── __init__.py
│   ├── models.py
│   └── routes.py
├── criar_dados.py
├── requirements.txt
└── run.py
```

## Como executar localmente

### 1. Clonar o repositório

```bash
git clone https://github.com/laviniasa/TPL.git
cd TPL
```

### 2. Criar e ativar o ambiente virtual

No Windows:

```bash
python -m venv venv
venv\Scripts\activate
```

No Linux/macOS:

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Instalar as dependências

```bash
pip install -r requirements.txt
```

### 4. Executar a aplicação

```bash
python run.py
```

Depois, abra no navegador o endereço informado pelo Flask, normalmente:

```text
http://127.0.0.1:5000
```

## Observações

O projeto está em desenvolvimento. Algumas funções administrativas e a importação completa da programação semanal ainda fazem parte das próximas etapas.

## Status

🚧 **Em desenvolvimento**

## Autora

Desenvolvido por **Lavínia Sá**.

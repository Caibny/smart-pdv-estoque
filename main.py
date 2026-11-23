import os
import shutil
import sqlite3
import time
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

os.makedirs("static/uploads", exist_ok=True)

app = FastAPI(title="Pacheco Distribuidora e Espeteria - PDV, Comanda & BI")
app.mount("/static", StaticFiles(directory="static"), name="static")

def get_db_connection():
    conn = sqlite3.connect("adega.db")
    conn.row_factory = sqlite3.Row
    return conn

def verificar_e_corrigir_banco():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cod_barras TEXT UNIQUE,
            nome TEXT,
            custo REAL,
            venda REAL,
            estoque INTEGER,
            foto_url TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vendas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT,
            total REAL,
            desconto REAL DEFAULT 0,
            forma_pagamento TEXT,
            mesa TEXT DEFAULT 'BALCÃO',
            cliente TEXT DEFAULT '',
            atendente TEXT DEFAULT 'Caixa',
            obs_geral TEXT DEFAULT ''
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS itens_venda (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venda_id INTEGER,
            produto_id INTEGER,
            nome_produto TEXT,
            quantidade INTEGER,
            custo_unitario REAL DEFAULT 0,
            preco_unitario REAL,
            subtotal REAL,
            obs_item TEXT DEFAULT '',
            FOREIGN KEY(venda_id) REFERENCES vendas(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config_comanda (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            titulo TEXT,
            subtitulo TEXT,
            atendente_padrao TEXT,
            rodape TEXT,
            largura_mm TEXT DEFAULT '80mm',
            imprimir_auto INTEGER DEFAULT 1
        )
    ''')

    # Insere configuração padrão da Pacheco se ainda não existir
    cursor.execute("SELECT COUNT(*) as qtd FROM config_comanda")
    if cursor.fetchone()["qtd"] == 0:
        cursor.execute('''
            INSERT INTO config_comanda (id, titulo, subtitulo, atendente_padrao, rodape, largura_mm, imprimir_auto)
            VALUES (1, 'Pacheco', 'Distribuidora e Espeteria', 'Atendente 01', 'Obrigado pela preferência!\nVolte sempre! :)', '80mm', 1)
        ''')

    # Migrações automáticas para colunas novas
    cursor.execute("PRAGMA table_info(vendas)")
    cols_vendas = [col["name"] for col in cursor.fetchall()]
    for col_name, col_def in [
        ("desconto", "REAL DEFAULT 0"),
        ("mesa", "TEXT DEFAULT 'BALCÃO'"),
        ("cliente", "TEXT DEFAULT ''"),
        ("atendente", "TEXT DEFAULT 'Caixa'"),
        ("obs_geral", "TEXT DEFAULT ''")
    ]:
        if col_name not in cols_vendas:
            cursor.execute(f"ALTER TABLE vendas ADD COLUMN {col_name} {col_def}")

    cursor.execute("PRAGMA table_info(itens_venda)")
    cols_itens = [col["name"] for col in cursor.fetchall()]
    if "custo_unitario" not in cols_itens:
        cursor.execute("ALTER TABLE itens_venda ADD COLUMN custo_unitario REAL DEFAULT 0")
    if "obs_item" not in cols_itens:
        cursor.execute("ALTER TABLE itens_venda ADD COLUMN obs_item TEXT DEFAULT ''")

    conn.commit()
    conn.close()

verificar_e_corrigir_banco()

@app.get("/")
def renderizar_sistema():
    try:
        with open("static/index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Erro: Arquivo static/index.html não encontrado.</h1>")

# --- MÓDULO: CONFIGURAÇÃO DA COMANDA ---
class ConfigComandaModel(BaseModel):
    titulo: str
    subtitulo: str
    atendente_padrao: str
    rodape: str
    largura_mm: str
    imprimir_auto: int

@app.get("/api/comanda/config")
def obter_config_comanda():
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM config_comanda WHERE id = 1").fetchone()
    conn.close()
    return dict(row)

@app.post("/api/comanda/config")
def salvar_config_comanda(cfg: ConfigComandaModel):
    conn = get_db_connection()
    conn.execute('''
        UPDATE config_comanda 
        SET titulo=?, subtitulo=?, atendente_padrao=?, rodape=?, largura_mm=?, imprimir_auto=?
        WHERE id = 1
    ''', (cfg.titulo, cfg.subtitulo, cfg.atendente_padrao, cfg.rodape, cfg.largura_mm, cfg.imprimir_auto))
    conn.commit()
    conn.close()
    return {"mensagem": "Layout da comanda atualizado!"}

# --- MÓDULO: PRODUTOS ---
@app.get("/api/produtos")
def listar_produtos():
    conn = get_db_connection()
    produtos = [dict(row) for row in conn.execute("SELECT * FROM produtos ORDER BY nome ASC").fetchall()]
    conn.close()
    return produtos

@app.post("/api/produtos")
async def cadastrar_produto(
    cod_barras: str = Form(""), nome: str = Form(...), custo: str = Form(...),
    venda: str = Form(...), estoque: int = Form(...), foto: UploadFile = File(None)
):
    cod_barras_final = cod_barras.strip() or f"SKU-{int(time.time())}"
    try:
        c_float = float(custo.replace('R$', '').replace('.', '').replace(',', '.'))
        v_float = float(venda.replace('R$', '').replace('.', '').replace(',', '.'))
    except ValueError:
        raise HTTPException(400, "Valores numéricos inválidos.")

    if c_float < 0 or v_float <= 0:
        raise HTTPException(400, "Os valores devem ser positivos.")
    if c_float > v_float:
        raise HTTPException(400, "O preço de custo não pode ser maior que o preço de venda.")

    foto_url = None
    if foto and foto.filename:
        ext = os.path.splitext(foto.filename)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png', '.webp']:
            raise HTTPException(400, "Formato de imagem inválido.")
        nome_arq = f"{cod_barras_final}{ext}"
        with open(os.path.join("static", "uploads", nome_arq), "wb") as buffer:
            shutil.copyfileobj(foto.file, buffer)
        foto_url = f"/static/uploads/{nome_arq}"

    try:
        conn = get_db_connection()
        conn.execute(
            '''INSERT INTO produtos (cod_barras, nome, custo, venda, estoque, foto_url) VALUES (?, ?, ?, ?, ?, ?)''',
            (cod_barras_final, nome.strip().upper(), c_float, v_float, estoque, foto_url)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Código de barras já cadastrado.")
    finally:
        conn.close()
    return {"mensagem": "Produto salvo."}

@app.put("/api/produtos/{id}")
async def editar_produto(
    id: int, nome: str = Form(...), custo: str = Form(...),
    venda: str = Form(...), estoque: int = Form(...), foto: UploadFile = File(None)
):
    try:
        c_float = float(custo.replace('R$', '').replace('.', '').replace(',', '.'))
        v_float = float(venda.replace('R$', '').replace('.', '').replace(',', '.'))
    except ValueError:
        raise HTTPException(400, "Valores inválidos.")

    if c_float < 0 or v_float <= 0 or c_float > v_float:
        raise HTTPException(400, "Verifique os valores de custo e venda.")

    conn = get_db_connection()
    prod = conn.execute("SELECT cod_barras, foto_url FROM produtos WHERE id = ?", (id,)).fetchone()
    if not prod:
        conn.close()
        raise HTTPException(404, "Não encontrado.")
    
    foto_url = prod["foto_url"]
    if foto and foto.filename:
        ext = os.path.splitext(foto.filename)[1].lower()
        nome_arq = f"{prod['cod_barras']}{ext}"
        with open(os.path.join("static", "uploads", nome_arq), "wb") as buffer:
            shutil.copyfileobj(foto.file, buffer)
        foto_url = f"/static/uploads/{nome_arq}"

    conn.execute(
        '''UPDATE produtos SET nome=?, custo=?, venda=?, estoque=?, foto_url=? WHERE id=?''',
        (nome.strip().upper(), c_float, v_float, estoque, foto_url, id)
    )
    conn.commit()
    conn.close()
    return {"mensagem": "Atualizado!"}

@app.delete("/api/produtos/{id}")
def excluir_produto(id: int):
    conn = get_db_connection()
    conn.execute("DELETE FROM produtos WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"mensagem": "Excluído!"}

# --- MÓDULO: VENDAS & COMANDA ---
class ItemCarrinho(BaseModel):
    id: int
    quantidade: int
    obs: str = ""

class VendaRequest(BaseModel):
    itens: list[ItemCarrinho]
    forma_pagamento: str
    desconto: float = 0.0
    mesa: str = "BALCÃO"
    cliente: str = ""
    atendente: str = "Caixa"
    obs_geral: str = ""

@app.post("/api/vendas")
def finalizar_venda(venda: VendaRequest):
    if not venda.itens:
        raise HTTPException(400, "O carrinho está vazio.")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        total_bruto = 0.0
        itens_proc = []
        
        for item in venda.itens:
            prod = cursor.execute("SELECT * FROM produtos WHERE id = ?", (item.id,)).fetchone()
            if not prod or prod["estoque"] < item.quantidade:
                raise ValueError(f"Estoque insuficiente: {prod['nome'] if prod else 'Item'}")
            
            sub = prod["venda"] * item.quantidade
            total_bruto += sub
            itens_proc.append({
                "id": prod["id"],
                "nome": prod["nome"],
                "qtd": item.quantidade,
                "custo": prod["custo"],
                "preco": prod["venda"],
                "sub": sub,
                "obs": item.obs.strip()
            })
            cursor.execute("UPDATE produtos SET estoque = estoque - ? WHERE id = ?", (item.quantidade, prod["id"]))

        desconto_real = max(0.0, min(venda.desconto, total_bruto))
        total_final = total_bruto - desconto_real
        data_hora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data_hora_br = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        cursor.execute(
            '''INSERT INTO vendas (data_hora, total, desconto, forma_pagamento, mesa, cliente, atendente, obs_geral) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (data_hora_iso, total_final, desconto_real, venda.forma_pagamento, venda.mesa.upper(), venda.cliente, venda.atendente, venda.obs_geral)
        )
        venda_id = cursor.lastrowid

        for ip in itens_proc:
            cursor.execute(
                '''INSERT INTO itens_venda (venda_id, produto_id, nome_produto, quantidade, custo_unitario, preco_unitario, subtotal, obs_item)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (venda_id, ip["id"], ip["nome"], ip["qtd"], ip["custo"], ip["preco"], ip["sub"], ip["obs"])
            )
        conn.commit()
        
        return {
            "mensagem": "Venda finalizada!",
            "pedido_id": venda_id,
            "data_hora_br": data_hora_br,
            "mesa": venda.mesa.upper(),
            "cliente": venda.cliente,
            "atendente": venda.atendente,
            "obs_geral": venda.obs_geral,
            "forma_pagamento": venda.forma_pagamento,
            "desconto": desconto_real,
            "total": total_final,
            "itens": itens_proc
        }
        
    except ValueError as ve:
        conn.rollback()
        raise HTTPException(400, str(ve))
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"Erro interno: {str(e)}")
    finally:
        conn.close()

@app.get("/api/vendas/{venda_id}/comanda")
def obter_dados_comanda_venda(venda_id: int):
    conn = get_db_connection()
    venda = conn.execute("SELECT * FROM vendas WHERE id = ?", (venda_id,)).fetchone()
    if not venda:
        conn.close()
        raise HTTPException(404, "Pedido não encontrado.")
    itens = conn.execute("SELECT nome_produto as nome, quantidade as qtd, subtotal as sub, obs_item as obs FROM itens_venda WHERE venda_id = ?", (venda_id,)).fetchall()
    conn.close()
    
    dt = datetime.strptime(venda["data_hora"], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
    return {
        "pedido_id": venda["id"],
        "data_hora_br": dt,
        "mesa": venda["mesa"] or "BALCÃO",
        "cliente": venda["cliente"] or "",
        "atendente": venda["atendente"] or "Caixa",
        "obs_geral": venda["obs_geral"] or "",
        "forma_pagamento": venda["forma_pagamento"],
        "desconto": venda["desconto"],
        "total": venda["total"],
        "itens": [dict(i) for i in itens]
    }

# --- MÓDULO: SEGURANÇA (RESET) ---
class ResetRequest(BaseModel):
    senha: str

@app.delete("/api/vendas/reset")
def zerar_historico_vendas(req: ResetRequest):
    if req.senha != "PACHECO2026":
        raise HTTPException(status_code=403, detail="Senha de administrador incorreta!")
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM itens_venda")
        conn.execute("DELETE FROM vendas")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='vendas' OR name='itens_venda'")
        conn.commit()
        return {"mensagem": "Faturamento zerado com sucesso!"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# --- MÓDULO: BI & DASHBOARD ---
@app.get("/api/dashboard")
def dados_dashboard(inicio: str = None, fim: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    hoje = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT SUM(total) as fat FROM vendas WHERE data_hora LIKE ?", (f"{hoje}%",))
    fat_hoje = cursor.fetchone()["fat"] or 0.0

    if inicio and fim:
        filtro = "data_hora >= ? AND data_hora <= ?"
        params = (f"{inicio} 00:00:00", f"{fim} 23:59:59")
    else:
        mes_atual = datetime.now().strftime("%Y-%m")
        filtro = "data_hora LIKE ?"
        params = (f"{mes_atual}%",)

    cursor.execute(
        f"SELECT SUM(v.total) as fat_periodo, COUNT(DISTINCT v.id) as qtd_vendas, SUM(v.desconto) as descontos_totais FROM vendas v WHERE v.{filtro}",
        params
    )
    res = cursor.fetchone()
    fat_periodo = res["fat_periodo"] or 0.0
    qtd_vendas = res["qtd_vendas"] or 0
    descontos = res["descontos_totais"] or 0.0
    ticket_medio = fat_periodo / qtd_vendas if qtd_vendas > 0 else 0.0

    filtro_join = filtro.replace("data_hora", "v.data_hora")
    cursor.execute(
        f"SELECT SUM((iv.preco_unitario - iv.custo_unitario) * iv.quantidade) as lucro_bruto_itens FROM itens_venda iv JOIN vendas v ON iv.venda_id = v.id WHERE {filtro_join}",
        params
    )
    lucro_periodo = (cursor.fetchone()["lucro_bruto_itens"] or 0.0) - descontos

    cursor.execute(f"SELECT forma_pagamento, SUM(total) as total FROM vendas WHERE {filtro} GROUP BY forma_pagamento", params)
    pgto_stats = [dict(row) for row in cursor.fetchall()]

    cursor.execute(
        f"SELECT iv.nome_produto, SUM(iv.quantidade) as total_vendido FROM itens_venda iv JOIN vendas v ON iv.venda_id = v.id WHERE {filtro_join} GROUP BY iv.produto_id ORDER BY total_vendido DESC LIMIT 5",
        params
    )
    top_produtos = [dict(row) for row in cursor.fetchall()]

    cursor.execute(
        f"SELECT strftime('%H', v.data_hora) as hora, SUM(v.total) as total FROM vendas v WHERE {filtro_join} GROUP BY hora ORDER BY hora",
        params
    )
    vendas_hora = [dict(row) for row in cursor.fetchall()]

    cursor.execute(
        f"SELECT iv.venda_id, iv.nome_produto, iv.quantidade, iv.preco_unitario, iv.subtotal, v.data_hora FROM itens_venda iv JOIN vendas v ON iv.venda_id = v.id WHERE {filtro_join} ORDER BY iv.id DESC LIMIT 100",
        params
    )
    historico = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return {
        "fat_hoje": fat_hoje, "fat_periodo": fat_periodo, "lucro_periodo": lucro_periodo,
        "ticket_medio": ticket_medio, "top_produtos": top_produtos, "pgto_stats": pgto_stats,
        "historico": historico, "vendas_hora": vendas_hora
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
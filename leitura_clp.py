import snap7
from snap7.util import get_int
import requests
from datetime import datetime, timedelta
import time

IP_CLP = '20.1.1.1'
RACK = 0
SLOT = 1
DB_NUMERO = 38

FIREBASE_URL = 'https://abaco-62f10-default-rtdb.firebaseio.com/'

# Mapeamento dos offsets na DB38
MAPA_DOENCAS = {
    "contaminacao": 2
}

plc = snap7.client.Client()

def conectar_clp():
    try:
        plc.connect(IP_CLP, RACK, SLOT)
        if plc.get_connected():
            print("Conectado ao S7-1200 com sucesso!")
            return True
    except:
        pass
    print("Falha ao conectar no CLP. Tentando novamente...")
    return False

def ler_dados_clp():
    tamanho_leitura = 46  # Lê do byte 0 até o 45 (total de 46 bytes)
    dados = plc.read_area(0x84, DB_NUMERO, 0, tamanho_leitura)
    valores = {}
    
    for nome_doenca, byte_inicio in MAPA_DOENCAS.items():
        valores[nome_doenca] = get_int(dados, byte_inicio)
        
    valores["Frangos_Hora"] = get_int(dados, 44)
    
    return valores

def obter_chave_banco():
    agora = datetime.now()
    hora_minuto = agora.hour * 100 + agora.minute
    
    # Nova regra de turnos ajustada:
    # - Turno 1 (T1): das 03:00 às 13:39 (do próprio dia)
    # - Turno 2 (T2): das 13:40 até às 02:59 do dia seguinte (se for entre 00:00 e 03:00, pertence ao dia lógico anterior)
    
    if hora_minuto >= 1340:
        turno = "T2"
        data_logica = agora
    elif hora_minuto < 300:
        turno = "T2"
        data_logica = agora - timedelta(days=1)
    else:
        turno = "T1"
        data_logica = agora
        
    return f"{data_logica.strftime('%Y-%m-%d')}_{turno}"

def atualizar_totais_firebase(valores_atuais):
    chave_atual = obter_chave_banco()
    url_dia = f"{FIREBASE_URL}{chave_atual}.json"
    
    payload = valores_atuais.copy()
    payload["ultima_atualizacao"] = datetime.now().strftime("%H:%M:%S")
    
    try:
        requests.patch(url_dia, json=payload)
    except Exception:
        pass

def registrar_ocorrencia(chave_str, nome_doenca, frangos_hora, quantidade):
    url = f"{FIREBASE_URL}{chave_str}/logs.json"
    payload = {
        "hora": datetime.now().strftime("%H:%M:%S"),
        "doenca": nome_doenca,
        "frangos_hora": frangos_hora,
        "quantidade": quantidade
    }
    try:
        requests.post(url, json=payload)
    except:
        pass

ultimo_estado = {}
ultima_chave = ""

conectar_clp()

try:
    print(f"Monitorando turnos, gráficos e contaminações na DB38...")
    while True:
        if not plc.get_connected():
            conectar_clp()
            time.sleep(3)
            continue

        try:
            estado_atual = ler_dados_clp()
            chave_hoje = obter_chave_banco()
            
            if ultimo_estado and chave_hoje == ultima_chave:
                for doenca in MAPA_DOENCAS.keys():
                    if estado_atual[doenca] > ultimo_estado.get(doenca, 0):
                        quantidade = estado_atual[doenca] - ultimo_estado.get(doenca, 0)
                        print(f"🚨 Nova ocorrência: {doenca} (+{quantidade}) às {datetime.now().strftime('%H:%M:%S')}")
                        registrar_ocorrencia(chave_hoje, doenca, estado_atual["Frangos_Hora"], quantidade)

            if estado_atual != ultimo_estado or chave_hoje != ultima_chave:
                atualizar_totais_firebase(estado_atual)
                ultimo_estado = estado_atual.copy()
                ultima_chave = chave_hoje
                
        except Exception as e:
            print(f"Erro no ciclo: {e}")
        
        time.sleep(2)

except KeyboardInterrupt:
    print("\nEncerrando comunicação...")
    plc.disconnect()
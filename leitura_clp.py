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

# Mapeamento dos offsets na DB38 para cada ábaco
# prefixo_firebase: '' grava na raiz (mantém histórico atual) | 'abaco_2/' grava em nó separado
CONFIG_ABACOS = {
    "abaco_1": {
        "nome_display": "Ábaco 1 (PCC-1B)",
        "prefixo_firebase": "",
        "offsets": {
            "contaminacao": 2
        }
    },
    "abaco_2": {
        "nome_display": "Ábaco 2 (Offset 72)",
        "prefixo_firebase": "abaco_2/",
        "offsets": {
            "contaminacao": 72
        }
    }
}

plc = snap7.client.Client()


def conectar_clp():
    try:
        plc.connect(IP_CLP, RACK, SLOT)
        if plc.get_connected():
            print("Conectado ao S7-1200 com sucesso!")
            return True
    except Exception:
        pass
    print("Falha ao conectar no CLP. Tentando novamente...")
    return False


def ler_dados_clp():
    # Lê do byte 0 até o 73 (74 bytes no total para alcançar o INT no offset 72)
    tamanho_leitura = 74
    dados = plc.read_area(0x84, DB_NUMERO, 0, tamanho_leitura)

    leitura = {
        "Frangos_Hora": get_int(dados, 44),
        "abaco_1": {},
        "abaco_2": {}
    }

    for abaco_id, cfg in CONFIG_ABACOS.items():
        for nome_doenca, byte_inicio in cfg["offsets"].items():
            leitura[abaco_id][nome_doenca] = get_int(dados, byte_inicio)

    return leitura


def obter_chave_banco():
    agora = datetime.now()
    hora_minuto = agora.hour * 100 + agora.minute

    # Turno 1 (T1): das 03:00 às 13:39 (do próprio dia)
    # Turno 2 (T2): das 13:40 até às 02:59 do dia seguinte
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


def buscar_totais_atuais_firebase(abaco_id, chave_str):
    cfg = CONFIG_ABACOS[abaco_id]
    url = f"{FIREBASE_URL}{cfg['prefixo_firebase']}{chave_str}.json"
    totais = {doenca: 0 for doenca in cfg["offsets"].keys()}

    try:
        resp = requests.get(url, timeout=5)
        dados = resp.json()
        if dados:
            for doenca in cfg["offsets"].keys():
                totais[doenca] = int(dados.get(doenca, 0))
    except Exception:
        pass
    return totais


def atualizar_totais_firebase(abaco_id, chave_atual, valores_atuais):
    cfg = CONFIG_ABACOS[abaco_id]
    url_dia = f"{FIREBASE_URL}{cfg['prefixo_firebase']}{chave_atual}.json"

    payload = valores_atuais.copy()
    payload["ultima_atualizacao"] = datetime.now().strftime("%H:%M:%S")

    try:
        requests.patch(url_dia, json=payload, timeout=5)
    except Exception:
        pass


def registrar_ocorrencia(abaco_id, chave_str, nome_doenca, frangos_hora, quantidade):
    cfg = CONFIG_ABACOS[abaco_id]
    url = f"{FIREBASE_URL}{cfg['prefixo_firebase']}{chave_str}/logs.json"

    payload = {
        "hora": datetime.now().strftime("%H:%M:%S"),
        "doenca": nome_doenca,
        "frangos_hora": frangos_hora,
        "quantidade": quantidade
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass


ultimo_estado = {}
ultima_chave = ""
totais_banco = {
    abaco_id: {doenca: 0 for doenca in cfg["offsets"].keys()}
    for abaco_id, cfg in CONFIG_ABACOS.items()
}

conectar_clp()

try:
    print("Monitorando turnos, gráficos e contaminações (Offsets 2 e 72) na DB38...")
    while True:
        if not plc.get_connected():
            conectar_clp()
            time.sleep(3)
            continue

        try:
            estado_atual = ler_dados_clp()
            chave_hoje = obter_chave_banco()

            # Se iniciou agora ou mudou de turno, busca os totais já salvos no Firebase para ambos os ábacos
            if chave_hoje != ultima_chave:
                for abaco_id in CONFIG_ABACOS.keys():
                    totais_banco[abaco_id] = buscar_totais_atuais_firebase(abaco_id, chave_hoje)
                    payload_inicial = totais_banco[abaco_id].copy()
                    payload_inicial["Frangos_Hora"] = estado_atual["Frangos_Hora"]
                    atualizar_totais_firebase(abaco_id, chave_hoje, payload_inicial)

                ultima_chave = chave_hoje
                ultimo_estado = {
                    "Frangos_Hora": estado_atual["Frangos_Hora"],
                    "abaco_1": estado_atual["abaco_1"].copy(),
                    "abaco_2": estado_atual["abaco_2"].copy()
                }
                time.sleep(2)
                continue

            vel_alterada = estado_atual["Frangos_Hora"] != ultimo_estado.get("Frangos_Hora")

            # Processa cada ábaco de forma independente
            for abaco_id, cfg in CONFIG_ABACOS.items():
                houve_alteracao = False

                for doenca in cfg["offsets"].keys():
                    val_atual = estado_atual[abaco_id][doenca]
                    val_anterior = ultimo_estado.get(abaco_id, {}).get(doenca, 0)

                    if val_atual > val_anterior:
                        quantidade = val_atual - val_anterior
                        totais_banco[abaco_id][doenca] += quantidade
                        houve_alteracao = True
                        print(f"🚨 [{cfg['nome_display']}] Nova ocorrência: {doenca} (+{quantidade}) às {datetime.now().strftime('%H:%M:%S')}")
                        registrar_ocorrencia(abaco_id, chave_hoje, doenca, estado_atual["Frangos_Hora"], quantidade)

                    elif val_atual < val_anterior:
                        print(f"🔄 [{cfg['nome_display']}] Contador do CLP zerado ({doenca}). Total do banco mantido em {totais_banco[abaco_id][doenca]}.")

                if houve_alteracao or vel_alterada:
                    payload = totais_banco[abaco_id].copy()
                    payload["Frangos_Hora"] = estado_atual["Frangos_Hora"]
                    atualizar_totais_firebase(abaco_id, chave_hoje, payload)

            ultimo_estado = {
                "Frangos_Hora": estado_atual["Frangos_Hora"],
                "abaco_1": estado_atual["abaco_1"].copy(),
                "abaco_2": estado_atual["abaco_2"].copy()
            }

        except Exception as e:
            print(f"Erro no ciclo: {e}")

        time.sleep(2)

except KeyboardInterrupt:
    print("\nEncerrando comunicação...")
    plc.disconnect()
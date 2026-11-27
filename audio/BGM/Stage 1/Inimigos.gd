extends CharacterBody2D
class_name Inimigos

## Funcionalidade comum para inimigos controlados pela IA.
## Os scripts específicos continuam responsáveis por decidir quando chamar
## movimento, gravidade, atualização de hitbox e animações.

@export_group("Combate")
@export var vida_maxima: int = 3
@export var dano: int = 1
@export var knockback_horizontal: float = 250.0
@export var knockback_vertical: float = 150.0
@export var gravidade: float = 980.0

@export_group("Detecção")
@export var distancia_deteccao: float = 500.0

@onready var sprite: AnimatedSprite2D = get_node_or_null("Sprite") as AnimatedSprite2D
@onready var hurtbox: Area2D = get_node_or_null("Hurtbox") as Area2D
@onready var hitbox: Area2D = get_node_or_null("Hitbox") as Area2D
@onready var hitbox_shape: CollisionShape2D = get_node_or_null("Hitbox/Colisão") as CollisionShape2D
@onready var hurtbox_shape: CollisionShape2D = get_node_or_null("Hurtbox/Colisão") as CollisionShape2D
@onready var physics_shape: CollisionShape2D = get_node_or_null("Colisão") as CollisionShape2D

var vida: int
var reimu: CharacterBody2D
var inimigo_morto: bool = false


func _ready() -> void:
	vida = vida_maxima
	add_to_group("enemies")
	reimu = get_tree().get_first_node_in_group("player") as CharacterBody2D

	if hitbox_shape:
		hitbox_shape.set_deferred("disabled", true)
	if hitbox:
		hitbox.set_deferred("monitoring", false)
	if hurtbox and not hurtbox.area_entered.is_connected(_ao_area_entrar_na_hurtbox):
		hurtbox.area_entered.connect(_ao_area_entrar_na_hurtbox)


## Aplica gravidade ao inimigo. Chame no _physics_process da classe derivada.
func aplicar_gravidade(delta: float) -> void:
	if not is_on_floor():
		velocity.y += gravidade * delta


## Retorna a direção horizontal e a distância até Reimu, atualizando a
## referência caso o jogador ainda não estivesse disponível no _ready.
func detectar_reimu() -> Dictionary:
	if not is_instance_valid(reimu):
		reimu = get_tree().get_first_node_in_group("player") as CharacterBody2D
	if not is_instance_valid(reimu):
		return {"detectada": false, "direcao": 0.0, "distancia": INF}

	var distancia := global_position.distance_to(reimu.global_position)
	return {
		"detectada": distancia <= distancia_deteccao,
		"direcao": signf(reimu.global_position.x - global_position.x),
		"distancia": distancia
	}


## Move horizontalmente na direção de Reimu, respeitando a distância de
## detecção. A classe derivada decide a velocidade e a animação.
func mover_ate_reimu(velocidade: float) -> bool:
	var deteccao := detectar_reimu()
	if not deteccao.detectada:
		velocity.x = 0.0
		return false
	velocity.x = deteccao.direcao * velocidade
	return true


## Permite às classes derivadas habilitar a hitbox apenas nos frames de ataque.
func definir_hitbox_ativa(ativa: bool) -> void:
	if hitbox_shape:
		hitbox_shape.set_deferred("disabled", not ativa)
	if hitbox:
		hitbox.set_deferred("monitoring", ativa)


func desativar_hitbox() -> void:
	definir_hitbox_ativa(false)


## Acerta Reimu uma única vez por ataque. Limpe a lista no início de cada golpe.
func acertar_reimu(area: Area2D, alvos_atingidos: Array[Node]) -> void:
	if inimigo_morto or not area.is_in_group("player_hurtbox"):
		return
	var alvo := area.get_parent()
	if not is_instance_valid(alvo) or not alvo.is_in_group("player") or alvo in alvos_atingidos:
		return
	alvos_atingidos.append(alvo)
	if alvo.has_method("receber_dano"):
		alvo.receber_dano(dano, global_position)


## Use como callback do sinal area_entered da hitbox ou durante a verificação
## contínua de sobreposição.
func ao_acertar_hitbox(area: Area2D, alvos_atingidos: Array[Node]) -> void:
	acertar_reimu(area, alvos_atingidos)


## Recebe golpes da hitbox de Reimu, incluindo o fallback para atacantes sem
## registrar_acerto().
func _ao_area_entrar_na_hurtbox(area: Area2D) -> void:
	if inimigo_morto or not area.is_in_group("player_hitbox"):
		return
	var atacante := area.get_parent()
	if not is_instance_valid(atacante):
		return
	if atacante.has_method("registrar_acerto"):
		atacante.registrar_acerto(self)
		return
	var dano_recebido: int = 1
	var valor = atacante.get("dano_ataque")
	if valor != null:
		dano_recebido = int(valor)
	receber_dano(dano_recebido, atacante.global_position)


func receber_dano(valor: int, origem: Vector2) -> void:
	if inimigo_morto or valor <= 0:
		return
	vida = maxi(0, vida - valor)
	if vida == 0:
		morrer()
		return

	_desativar_areas_de_combate()
	var direcao := signf(global_position.x - origem.x)
	if is_zero_approx(direcao):
		direcao = 1.0 if sprite and sprite.flip_h else -1.0
	velocity.x = direcao * knockback_horizontal
	velocity.y = -knockback_vertical
	if sprite and sprite.sprite_frames and sprite.sprite_frames.has_animation("hurt"):
		sprite.play("hurt")


func morrer() -> void:
	if inimigo_morto:
		return
	inimigo_morto = true
	_desativar_areas_de_combate()
	if hurtbox:
		hurtbox.set_deferred("monitoring", false)
		hurtbox.set_deferred("monitorable", false)
	if hurtbox_shape:
		hurtbox_shape.set_deferred("disabled", true)
	if physics_shape:
		physics_shape.set_deferred("disabled", true)
	velocity = Vector2.ZERO
	if sprite and sprite.sprite_frames and sprite.sprite_frames.has_animation("die"):
		sprite.play("die")
	else:
		queue_free()


## Conecte ao animation_finished da animação do inimigo.
func finalizar_animacao_morte() -> void:
	if inimigo_morto and sprite and sprite.animation == "die":
		queue_free()


func _desativar_areas_de_combate() -> void:
	desativar_hitbox()


extends CharacterBody2D

@onready var animReimu: AnimatedSprite2D = $Sprite
@onready var hitbox: Area2D = $Hitbox
@onready var hitbox_shape: CollisionShape2D = $Hitbox/Colisão

# Movimento
@export_group("Movimento")
@export var speed: float = 250.0
@export var run_multiplier: float = 2.5
@export var jump_velocity: float = -500.0
@export var dash_brake_rate: float = 2000.0

# Vida
@export_group("Vida")
@export var vida_maxima: int = 5
var vida: int

# Dano e knockback
@export_group("Dano")
@export var tempo_invulnerabilidade: float = 1.5
@export var knockback_horizontal: float = 400.0
@export var knockback_vertical: float = 250.0
@export var knockback_friction: float = 1800.0

# Ataques
@export_group("Combate")
@export var dano_ataque: int = 1

@export_group("Frames de ataque")
@export var frames_attack_01: Array[int] = []
@export var frames_attack_02: Array[int] = []
@export var frames_attack_air: Array[int] = []

@export_group("Hitbox")
var hitbox_offset_editor: Vector2

enum EstadoAnim {
	IDLE,
	WALK_S,
	WALK_L,
	WALK_E,
	JUMP_S,
	JUMP_L,
	JUMP_E,
	DASH_S,
	DASH_L,
	DASH_E,
	ATTACK_01,
	ATTACK_02,
	ATTACK_AIR,
	FALLING,
	HIT
}

var estadoAnimacao = EstadoAnim.IDLE

var invulneravel: bool = false
var tempo_invulneravel: float = 0.0

var freando_dash: bool = false
var combo_pendente: bool = false


func _ready() -> void:
	vida = vida_maxima

	hitbox_offset_editor = hitbox_shape.position

	hitbox.add_to_group("player_hitbox")
	hitbox_shape.disabled = true
	hitbox.monitoring = false

	if not animReimu.frame_changed.is_connected(_atualizar_hitbox):
		animReimu.frame_changed.connect(_atualizar_hitbox)

	if not animReimu.animation_finished.is_connected(_on_sprite_animation_finished):
		animReimu.animation_finished.connect(_on_sprite_animation_finished)

	_atualizar_orientacao_hitbox()


func _physics_process(delta: float) -> void:
	var currentAnim: String
	var direction = Input.get_axis("left", "right")
	var correndo = Input.is_action_pressed("run") and direction != 0

	# Invulnerabilidade
	if invulneravel:
		tempo_invulneravel -= delta

		if tempo_invulneravel <= 0:
			invulneravel = false
			tempo_invulneravel = 0.0

	# Entrada de ataque
	if Input.is_action_just_pressed("attack"):
		if estadoAnimacao == EstadoAnim.ATTACK_01:
			combo_pendente = true

		elif estadoAnimacao not in [
			EstadoAnim.ATTACK_01,
			EstadoAnim.ATTACK_02,
			EstadoAnim.ATTACK_AIR,
			EstadoAnim.HIT
		]:
			combo_pendente = false

			if is_on_floor():
				estadoAnimacao = EstadoAnim.ATTACK_01
			else:
				estadoAnimacao = EstadoAnim.ATTACK_AIR

	# Estados de ação
	var atacando = estadoAnimacao in [
		EstadoAnim.ATTACK_01,
		EstadoAnim.ATTACK_02,
		EstadoAnim.ATTACK_AIR
	]

	var recebendo_hit = estadoAnimacao == EstadoAnim.HIT

	# Detecta o encerramento da corrida
	if not recebendo_hit and not atacando:
		if is_on_floor():
			if estadoAnimacao in [EstadoAnim.DASH_S, EstadoAnim.DASH_L]:
				if not correndo:
					estadoAnimacao = EstadoAnim.DASH_E
					freando_dash = true

	# Retoma a corrida durante a frenagem
	if correndo and freando_dash and not recebendo_hit:
		freando_dash = false

		if not atacando:
			estadoAnimacao = EstadoAnim.DASH_S

	# Movimento horizontal
	if recebendo_hit:
		velocity.x = move_toward(
			velocity.x,
			0.0,
			knockback_friction * delta
		)

	elif freando_dash:
		var velocidade_alvo = 0.0

		if direction != 0 and signf(velocity.x) == signf(-direction):
			velocidade_alvo = -direction * speed

		velocity.x = move_toward(
			velocity.x,
			velocidade_alvo,
			dash_brake_rate * delta
		)

		if is_zero_approx(velocity.x) or is_equal_approx(velocity.x, velocidade_alvo):
			velocity.x = velocidade_alvo
			freando_dash = false

	else:
		var velocidade_atual = speed

		if correndo:
			velocidade_atual *= run_multiplier

		velocity.x = -direction * velocidade_atual

	# Orientação
	if direction != 0 and not freando_dash and not recebendo_hit:
		animReimu.flip_h = direction > 0
		_atualizar_orientacao_hitbox()

	# Gravidade
	if not is_on_floor():
		velocity += get_gravity() * delta
	elif velocity.y > 0:
		velocity.y = 0

	# Pulo
	if not atacando and not recebendo_hit:
		if Input.is_action_just_pressed("jump") and is_on_floor():
			velocity.y = jump_velocity
			estadoAnimacao = EstadoAnim.JUMP_S

	move_and_slide()

	# Atualização dos estados de movimento
	if not atacando and not recebendo_hit:
		if not is_on_floor():
			if estadoAnimacao in [
				EstadoAnim.IDLE,
				EstadoAnim.WALK_S,
				EstadoAnim.WALK_L,
				EstadoAnim.WALK_E,
				EstadoAnim.DASH_S,
				EstadoAnim.DASH_L,
				EstadoAnim.DASH_E
			]:
				estadoAnimacao = EstadoAnim.FALLING

			elif velocity.y >= 0:
				if estadoAnimacao in [EstadoAnim.JUMP_S, EstadoAnim.JUMP_L]:
					estadoAnimacao = EstadoAnim.JUMP_E

		else:
			if estadoAnimacao in [
				EstadoAnim.JUMP_S,
				EstadoAnim.JUMP_L,
				EstadoAnim.JUMP_E,
				EstadoAnim.FALLING
			]:
				freando_dash = false

				if correndo:
					estadoAnimacao = EstadoAnim.DASH_S
				elif direction != 0:
					estadoAnimacao = EstadoAnim.WALK_S
				else:
					estadoAnimacao = EstadoAnim.IDLE

			elif estadoAnimacao == EstadoAnim.DASH_E:
				pass

			elif correndo:
				if estadoAnimacao not in [EstadoAnim.DASH_S, EstadoAnim.DASH_L]:
					estadoAnimacao = EstadoAnim.DASH_S

			elif estadoAnimacao in [EstadoAnim.DASH_S, EstadoAnim.DASH_L]:
				estadoAnimacao = EstadoAnim.DASH_E
				freando_dash = true

			elif not freando_dash:
				if direction != 0:
					if estadoAnimacao in [EstadoAnim.IDLE, EstadoAnim.WALK_E]:
						estadoAnimacao = EstadoAnim.WALK_S
				else:
					if estadoAnimacao in [EstadoAnim.WALK_S, EstadoAnim.WALK_L]:
						estadoAnimacao = EstadoAnim.WALK_E

	# Seleção da animação
	match estadoAnimacao:
		EstadoAnim.IDLE:
			currentAnim = "idle"
		EstadoAnim.WALK_S:
			currentAnim = "walk_s"
		EstadoAnim.WALK_L:
			currentAnim = "walk_l"
		EstadoAnim.WALK_E:
			currentAnim = "walk_e"
		EstadoAnim.JUMP_S:
			currentAnim = "jump_s"
		EstadoAnim.JUMP_L:
			currentAnim = "jump_l"
		EstadoAnim.JUMP_E:
			currentAnim = "jump_e"
		EstadoAnim.FALLING:
			currentAnim = "falling"
		EstadoAnim.DASH_S:
			currentAnim = "dash_s"
		EstadoAnim.DASH_L:
			currentAnim = "dash_l"
		EstadoAnim.DASH_E:
			currentAnim = "dash_e"
		EstadoAnim.ATTACK_01:
			currentAnim = "attack_01"
		EstadoAnim.ATTACK_02:
			currentAnim = "attack_02"
		EstadoAnim.ATTACK_AIR:
			currentAnim = "attack_air"
		EstadoAnim.HIT:
			currentAnim = "hit"

	if animReimu.animation != currentAnim:
		animReimu.play(currentAnim)
		_atualizar_hitbox()


# Recebe dano de inimigos
func receber_dano(dano: int, origem: Vector2) -> bool:
	if invulneravel or vida <= 0:
		return false

	vida = maxi(0, vida - dano)

	invulneravel = true
	tempo_invulneravel = tempo_invulnerabilidade

	combo_pendente = false
	freando_dash = false

	_desativar_hitbox()

	var direcao_knockback = signf(global_position.x - origem.x)

	if is_zero_approx(direcao_knockback):
		direcao_knockback = -1.0 if animReimu.flip_h else 1.0

	velocity.x = direcao_knockback * knockback_horizontal
	velocity.y = -knockback_vertical

	estadoAnimacao = EstadoAnim.HIT

	return true


# Atualiza a orientação da hitbox
func _atualizar_orientacao_hitbox() -> void:
	if animReimu.flip_h:
		hitbox_shape.position = Vector2(
			-hitbox_offset_editor.x,
			hitbox_offset_editor.y
		)
	else:
		hitbox_shape.position = hitbox_offset_editor


# Ativa a hitbox somente nos frames de impacto
func _atualizar_hitbox() -> void:
	if not is_instance_valid(hitbox_shape):
		return

	var frames_ativos: Array[int] = []

	match animReimu.animation:
		"attack_01":
			if estadoAnimacao == EstadoAnim.ATTACK_01:
				frames_ativos = frames_attack_01

		"attack_02":
			if estadoAnimacao == EstadoAnim.ATTACK_02:
				frames_ativos = frames_attack_02

		"attack_air":
			if estadoAnimacao == EstadoAnim.ATTACK_AIR:
				frames_ativos = frames_attack_air

	var frame_ativo = animReimu.frame in frames_ativos

	hitbox_shape.set_deferred("disabled", not frame_ativo)
	hitbox.set_deferred("monitoring", frame_ativo)


func _desativar_hitbox() -> void:
	hitbox_shape.set_deferred("disabled", true)
	hitbox.set_deferred("monitoring", false)


# Define o estado após uma ação terminar
func _finalizar_acao() -> void:
	var direction = Input.get_axis("left", "right")
	var correndo = Input.is_action_pressed("run") and direction != 0

	if not is_on_floor():
		if velocity.y < 0:
			estadoAnimacao = EstadoAnim.JUMP_L
		else:
			estadoAnimacao = EstadoAnim.FALLING

	elif freando_dash:
		estadoAnimacao = EstadoAnim.DASH_E

	elif correndo:
		estadoAnimacao = EstadoAnim.DASH_S

	elif direction != 0:
		estadoAnimacao = EstadoAnim.WALK_S

	else:
		estadoAnimacao = EstadoAnim.IDLE


# Eventos de fim de animação
func _on_sprite_animation_finished() -> void:
	var direction = Input.get_axis("left", "right")
	var correndo = Input.is_action_pressed("run") and direction != 0

	match animReimu.animation:
		"walk_s":
			if estadoAnimacao == EstadoAnim.WALK_S:
				if direction != 0 and is_on_floor():
					estadoAnimacao = EstadoAnim.WALK_L
				else:
					estadoAnimacao = EstadoAnim.WALK_E

		"walk_e":
			if estadoAnimacao == EstadoAnim.WALK_E:
				estadoAnimacao = EstadoAnim.IDLE

		"dash_s":
			if estadoAnimacao == EstadoAnim.DASH_S:
				if correndo and is_on_floor():
					estadoAnimacao = EstadoAnim.DASH_L
				else:
					estadoAnimacao = EstadoAnim.DASH_E
					freando_dash = true

		"dash_e":
			if estadoAnimacao == EstadoAnim.DASH_E:
				if correndo and is_on_floor():
					estadoAnimacao = EstadoAnim.DASH_S
					freando_dash = false
				elif direction != 0 and is_on_floor():
					estadoAnimacao = EstadoAnim.WALK_S
				else:
					estadoAnimacao = EstadoAnim.IDLE

		"jump_s":
			if estadoAnimacao == EstadoAnim.JUMP_S:
				if velocity.y < 0:
					estadoAnimacao = EstadoAnim.JUMP_L
				else:
					estadoAnimacao = EstadoAnim.JUMP_E

		"jump_e":
			if estadoAnimacao == EstadoAnim.JUMP_E:
				if not is_on_floor():
					estadoAnimacao = EstadoAnim.FALLING

		"attack_01":
			if estadoAnimacao == EstadoAnim.ATTACK_01:
				if combo_pendente:
					combo_pendente = false
					estadoAnimacao = EstadoAnim.ATTACK_02
				else:
					_finalizar_acao()

		"attack_02":
			if estadoAnimacao == EstadoAnim.ATTACK_02:
				_finalizar_acao()

		"attack_air":
			if estadoAnimacao == EstadoAnim.ATTACK_AIR:
				_finalizar_acao()

		"hit":
			if estadoAnimacao == EstadoAnim.HIT:
				_finalizar_acao()

	_desativar_hitbox()

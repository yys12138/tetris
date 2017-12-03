﻿import tensorflow as tf
import numpy as np
from collections import deque
import random
import copy
from game import Tetris
from play import TetrisUI

# TODO: 
# 1、完善模型，有几个思路：增加卷积层，为每个方块类型设置单独的矩阵参数，分离位置和旋转两个操作、但是还没有想好
# 2、将模型分为训练模型和目标模型（target），target用于在回顾记忆时，计算n+i的maxQ。尝试修改了两次，都没成功，需要研究一下sess和saver之类的接口。目前来看，这一条的影响似乎不是最大的。
# 3、奖励函数的算法，增加更详细的算法，更精确的评估操作

# 发现了一个问题，模型计算出来的权值，都nan了，貌似是卷积加全连接之后，结果太大。怎么解决还需要考虑，是变量初始值的问题，还是什么
# 也许需要通过输出，找到再网络的哪一层出现nan。现在看来，距离调整算法还有段距离呢，先把模型的bug调好

model = None
sess = None
saver = None

def weight_variable(shape):
	initial = tf.truncated_normal(shape, stddev=0.1)
	return tf.Variable(initial)

def bias_variable(shape):
	initial = tf.constant(0.1, shape=shape)
	return tf.Variable(initial)

def conv2d(x, W):
	return tf.nn.conv2d(x, W, strides=[1,1,1,1], padding='SAME')

def max_pool_2x2(x):
	return tf.nn.max_pool(x, ksize=[1,2,2,1], strides=[1,2,2,1], padding='SAME')

def create_model():
	model = tf.Graph()
	with model.as_default():
		#input
		_tiles = tf.placeholder(tf.float32, [None, 20, 10], name="tiles")
		_current = tf.placeholder(tf.float32, [None, 2], name="current") # idx, next_idx
		keep_prob = tf.placeholder(tf.float32, name="kp")
		print("_tiles", _tiles)
		print("_current", _current)

		#layer 1
		_tiles_reshape = tf.reshape(_tiles, [-1, 20, 10, 1])
		W_conv1 = weight_variable([5,5,1,32])
		b_conv1 = bias_variable([32])
		h_conv1 = tf.nn.relu(conv2d(_tiles_reshape, W_conv1) + b_conv1)
		h_pool1 = max_pool_2x2(h_conv1)
		print("h_pool1", h_pool1)

		#layer fc1
		W_fc1 = weight_variable([10 * 5 * 32 + 2, 1024])
		b_fc1 = bias_variable([1024])
		h_pool_flat = tf.reshape(h_pool1, [-1, 10 * 5 * 32])
		h_fc1_input = tf.concat([h_pool_flat, _current], 1)
		h_fc1 = tf.nn.relu(tf.matmul(h_fc1_input, W_fc1) + b_fc1)
		print("h_fc1_input", h_fc1_input)
		print("h_fc1", h_fc1)

		#drop out
		h_fc1_drop = tf.nn.dropout(h_fc1, keep_prob)
		print("h_fc1_drop", h_fc1_drop)

		#layer fc2 x * 4 + r
		W_fc2_xr = weight_variable([1024, 40])
		b_fc2_xr = bias_variable([40])

		# 这里如果使用softmax，那么最大值永远不会超过1，也就失去Q值得含义了
		#output = tf.nn.softmax(tf.matmul(h_fc1_drop, W_fc2_xr) + b_fc2_xr, name="output")	# this is the Q of each action
		output = tf.add(tf.matmul(h_fc1_drop, W_fc2_xr), b_fc2_xr, name="output")	# this is the Q of each action
		print("output", output)

	return model

def init_model(train = False, forceinit = False):
	global model
	global sess
	global saver
	model = create_model()
	if train:
		create_train_op(model)
	sess = tf.InteractiveSession(graph = model)
	saver = tf.train.Saver(max_to_keep = 1)
	cp = tf.train.latest_checkpoint('model_0/')
	if cp == None or forceinit:
		print("init model with default val")
		tf.global_variables_initializer().run()
		save_model()
	else:
		print("init model with saved val")
		saver.restore(sess, cp)

def save_model():
	global sess
	global saver
	saver.save(sess, 'model_0/save.ckpt')

__cur_step = -1
__cur_output = 0
def run_game(tetris):
	global model
	global sess
	global __cur_step
	global __cur_output
	if tetris.step() != __cur_step:
		tiles = [tetris.tiles()]
		current = [[tetris.current_index(), tetris.next_index()]]
		kp = 1
		output = model.get_tensor_by_name("output:0")
		__cur_output_xr = output.eval(feed_dict={"tiles:0":tiles, "current:0":current, "kp:0":kp})
		__cur_step = tetris.step()
		__cur_output = np.argmax(__cur_output_xr)
		print("step %d, output: %d, x: %d, r: %d" % (__cur_step, __cur_output, int(__cur_output / 4), int(__cur_output % 4)))
	
	x = int(__cur_output / 4)
	r = int(__cur_output % 4)
	if tetris.move_step_by_ai(x, r):
		tetris.fast_finish()


def create_train_op(model):
	with model.as_default():
		#train input
		_action = tf.placeholder(tf.float32, [None, 40], name="action")
		_targetQ = tf.placeholder(tf.float32, [None], name="targetQ") # reward + gamma * max(Q_sa)
		_learningRate = tf.placeholder(tf.float32, name="learningRate")

		#train
		output = model.get_tensor_by_name("output:0")
		Q = tf.reduce_sum(tf.multiply(output, _action), reduction_indices = 1)	# take the weight of _action in output as Q
		iterQ = Q + _learningRate * (_targetQ - Q)
		cost = tf.reduce_mean(tf.square(Q - iterQ), name="cost")
		optimizer = tf.train.GradientDescentOptimizer(0.5).minimize(cost, name="train_op")
		print("optimizer", optimizer)

	return model

def train(tetris
	, memory_size = 1000
	, batch_size = 1
	, train_steps = 10000
	, gamma = 0.8
	, init_epsilon = 1
	, min_epsilon = 0.01
	, learningRate_numerator = 100000
	, learningRate_denominator = 100000
	, ui = None):
	global model
	global sess
	D = deque()

	epsilon = init_epsilon
	step = 0
	last_cost = 0
	status_0 = train_make_status(tetris)
	while True:
		#run game
		action_0 = [0] * 40 # judge action, random or from model, this action vector must be onehot
		if random.random() < epsilon:
			action_0[random.randrange(40)] = 1
		else:
			idx = np.argmax(train_cal_action_weight([status_0], model)[0])
			action_0[idx] = 1
		epsilon = init_epsilon + (min_epsilon - init_epsilon) * step / train_steps
		learningRate = float(learningRate_numerator) / float(learningRate_denominator + step)

		gameover = train_run_game(tetris, action_0, ui)  #use the action to run, then get reward
		status_1 = train_make_status(tetris)
		reward_1, reward_info = train_cal_reward(tetris, status_0, status_1, gameover)
		
		#log to memory
		D.append((status_0, action_0, reward_1, status_1, gameover))
		if len(D) > memory_size:
			D.popleft()

		if ui != None:
			weight = train_cal_action_weight([status_0], model)[0]
			ui.log("action: %d, maxweight: %f, reward: %f, info: %s" % (np.argmax(action_0), np.max(weight), reward_1, reward_info))

		#review memory
		if len(D) > batch_size:
			batch = random.sample(D, batch_size)
			status_0_batch = [d[0] for d in batch]
			action_0_batch = [d[1] for d in batch]
			reward_1_batch = [d[2] for d in batch]
			status_1_batch = [d[3] for d in batch]
			gameover_1_batch = [d[4] for d in batch]

			Q_1_batch = train_cal_action_weight(status_1_batch, model)	#action_1 == Q_i+1

			targetQ_batch = []
			for i in range(len(batch)):
				if gameover_1_batch[i]:
					targetQ_batch.append(reward_1_batch[i])
				else:
					targetQ_batch.append(reward_1_batch[i] + gamma * np.max(Q_1_batch[i]))

			tiles = [status["tiles"] for status in status_0_batch]
			current = [status["current"] for status in status_0_batch]
			kp = 1
			train_op = model.get_operation_by_name("train_op")
			cost = model.get_tensor_by_name("cost:0")
			_, last_cost = sess.run((train_op, cost),
				feed_dict={"tiles:0":tiles, "current:0":current, "action:0":action_0_batch, "targetQ:0":targetQ_batch, "learningRate:0":learningRate, "kp:0":kp})

			if step % 100 == 0:
				info = "train step %d, epsilon: %f, learningRate: %f, cost: %f" % (step, epsilon, learningRate, last_cost)
				if ui == None:
					print(info)
					print(status_1_batch)
					print(Q_1_batch)
					print(targetQ_batch)
				else:
					ui.log(info)
				save_model()

		#loop
		status_0 = status_1
		step += 1
		if step > train_steps:
			break
		

def train_make_status(tetris):	# 0, tiles; 1, current
	tiles = copy.deepcopy(tetris.tiles())
	current = [tetris.current_index(), tetris.next_index()]
	score = tetris.score()
	status = {"tiles":tiles, "current":current, "score":score}
	return status

def train_cal_action_weight(status_s, use_model):
	global sess
	tiles = [status["tiles"] for status in status_s]
	current = [status["current"] for status in status_s]
	kp = 1
	argmax_xr = use_model.get_tensor_by_name("output:0").eval(feed_dict={"tiles:0":tiles, "current:0":current, "kp:0":kp})
	return argmax_xr

def train_run_game(tetris, action, ui):
	xr = np.argmax(action)
	x = int(xr / 4)
	r = int(xr % 4)

	while True:
		move_finish = tetris.move_step_by_ai(x, r)

		if ui != None:
			if ui.refresh_and_check_quit():
				raise Exception("user quit")

		if move_finish:
			tetris.fast_finish()
			break

	gameover = False
	if tetris.gameover():
		tetris.reset()
		gameover = True

	return gameover

def train_stat_tetris_info(status):
	row_cnt = 0
	total_fill = 0
	masked_tile_cnt = 0

	tiles = status["tiles"]
	top_y_index = [20] * len(tiles)	#top y indexs of status, for cal masked_tile_cnt
	for y in range(len(tiles)):
		row = tiles[y]
		row_fill = 0
		for x in range(len(row)):
			t = row[x]
			if t > 0:
				row_fill += 1
				top_y_index[x] = min(top_y_index[x], y)
			else:
				if y > top_y_index[x]:
					masked_tile_cnt += 1
		if row_fill > 0:
			row_cnt += 1
			total_fill += row_fill

	fill_rate = 0
	if row_cnt > 0:
		fill_rate = float(total_fill) / float(row_cnt * len(tiles[0]))

	return row_cnt, fill_rate, masked_tile_cnt

def train_cal_reward(tetris, status_0, status_1, gameover):
	# 希望统计的内容：
	# 行数的增量，被遮挡的空格数量，填充率
	if gameover:
		return -1000, ""

	row_cnt_0, row_fill_rate_0, masked_tile_cnt_0 = train_stat_tetris_info(status_0)
	row_cnt_1, row_fill_rate_1, masked_tile_cnt_1 = train_stat_tetris_info(status_1)

	inc_row = float(row_cnt_1 - row_cnt_0)
	inc_row_fill_rate = float(row_fill_rate_1 - row_fill_rate_0)
	inc_masked_tile_cnt = float(masked_tile_cnt_1 - masked_tile_cnt_0)
	erase_raw = float(tetris.last_erase_row())

	info = "%f + %f - %d - %d" % (erase_raw * 10, inc_row_fill_rate * 100, inc_row, inc_masked_tile_cnt * 2)

	return erase_raw * 10 + inc_row_fill_rate * 100 - inc_row - inc_masked_tile_cnt * 2, info

if __name__ == '__main__':
	init_model()
	save_model()

import scipy.io
import scipy.misc
from glob import glob
import os
import numpy as np
from image_utils import *
from ops import *
from sklearn.model_selection import train_test_split
import tensorflow as tf
from tensorflow import contrib


class DeepHeatmapsModel(object):

    """facial landmark localization Network"""

    def __init__(self, mode='TRAIN', train_iter=500000, learning_rate=1e-6, image_size=256, c_dim=3, batch_size=10,
                 num_landmarks=68, augment=True, img_path='data', save_log_path='logs', save_sample_path='sample',
                 save_model_path='model',test_model_path='model/deep_heatmaps_primary-1000'):

        # values to print to save parameter:

        # optimizer parameters
        momentum = 0.95
        step = 80000  # for lr decay
        gamma = 0.1  # for lr decay
        l_weight_primary = 1000.  # primary loss weight
        l_weight_fusion = 3. * l_weight_primary  # fusion loss weight

        # network init parameters
        weight_initializer = 'xavier'  # random_normal or xavier
        weight_initializer_std = 0.01
        bias_initializer = 0.0

        sigma = 6  # sigma for heatmap generation
        scale = '1'  # scale for image normalization '255' / '1' / '0'
        margin = 0.25  # for face crops
        bb_type = 'gt'  # gt/init

        valid_size = 100
        test_data = 'art'  # if mode is TEST, this choose the set to use full/common/challenging/test

        # sampling and logging parameters
        self.print_every = 10
        self.save_every = 5000
        self.sample_every_epoch = False
        self.sample_every = 1000
        self.sample_grid = 9
        self.log_every_epoch = 1
        self.log_histograms = True

        self.debug = False
        self.debug_data_size = 20
        self.compute_nme = True

        self.config = tf.ConfigProto()
        self.config.gpu_options.allow_growth = True

        self.mode = mode
        self.train_iter = train_iter
        self.learning_rate = learning_rate

        self.image_size = image_size
        self.c_dim = c_dim
        self.batch_size = batch_size

        self.num_landmarks = num_landmarks

        self.save_log_path = save_log_path
        self.save_sample_path = save_sample_path
        self.save_model_path = save_model_path
        self.test_model_path = test_model_path
        self.img_path=img_path

        self.momentum = momentum
        self.step = step  # for lr decay
        self.gamma = gamma  # for lr decay
        self.l_weight_primary = l_weight_primary  # primary loss weight
        self.l_weight_fusion = l_weight_fusion  # fusion loss weight

        self.weight_initializer = weight_initializer  # random_normal or xavier
        self.weight_initializer_std = weight_initializer_std
        self.bias_initializer = bias_initializer

        self.sigma = sigma  # sigma for heatmap generation
        self.scale = scale  # scale for image normalization '255' / '1' / '0'

        self.test_data =test_data  # if mode is TEST, this choose the set to use full/common/challenging/test

        # load image, bb and landmark data using menpo
        self.bb_dir = os.path.join(img_path, 'Bounding_Boxes')
        if mode == 'TEST' and test_data == 'art':
            self.compute_nme=False
            self.img_menpo_list = mio.import_images(os.path.join(img_path+'art_set'), verbose=True)
        else:
            self.bb_dictionary = load_bb_dictionary(self.bb_dir, mode, test_data=self.test_data)
            self.img_menpo_list = load_menpo_image_list(img_path, mode, self.bb_dictionary, image_size, augment=augment,
                                                        margin=margin, bb_type=bb_type, test_data=self.test_data)
        if self.debug:
            self.img_menpo_list = self.img_menpo_list[:self.debug_data_size]

        if mode is 'TRAIN':
            train_params = locals()
            print_training_params_to_file(train_params)  # save init parameters

            # train - validation split
            self.valid_size = valid_size
            np.random.seed(0)
            img_inds = np.arange(len(self.img_menpo_list))
            np.random.shuffle(img_inds)

            val_inds = img_inds[:self.valid_size]
            train_inds = img_inds[self.valid_size:]
            self.valid_img_menpo_list = self.img_menpo_list[val_inds]

            self.valid_images_loaded, _, _, self.valid_landmarks_loaded = \
                load_data(self.valid_img_menpo_list, np.arange(self.valid_size), image_size=self.image_size,
                          c_dim=self.c_dim, num_landmarks=self.num_landmarks, sigma=self.sigma, scale=self.scale,
                          save_landmarks=True, primary=False)

            self.img_menpo_list = self.img_menpo_list[train_inds]

    def add_placeholders(self):

        if self.mode == 'TEST':
                self.images = tf.placeholder(
                    tf.float32, [None, self.image_size, self.image_size, self.c_dim], 'images')

                self.heatmaps = tf.placeholder(
                    tf.float32, [None, self.image_size, self.image_size, self.num_landmarks], 'heatmaps')

                self.heatmaps_small = tf.placeholder(
                    tf.float32, [None, self.image_size/4, self.image_size/4, self.num_landmarks], 'heatmaps_small')
                if self.compute_nme:
                    self.lms = tf.placeholder(tf.float32, [None, self.num_landmarks, 2], 'lms')
                    self.pred_lms = tf.placeholder(tf.float32, [None, self.num_landmarks, 2], 'pred_lms')

        elif self.mode == 'TRAIN':
                self.images = tf.placeholder(
                    tf.float32, [None, self.image_size, self.image_size, self.c_dim], 'train_images')

                self.heatmaps = tf.placeholder(
                    tf.float32, [None, self.image_size, self.image_size, self.num_landmarks], 'train_heatmaps')

                self.heatmaps_small = tf.placeholder(
                    tf.float32, [None, self.image_size/4, self.image_size/4, self.num_landmarks], 'train_heatmaps_small')

                if self.compute_nme:
                    self.train_lms = tf.placeholder(tf.float32, [None, self.num_landmarks, 2], 'train_lms')
                    self.train_pred_lms = tf.placeholder(tf.float32, [None, self.num_landmarks, 2], 'train_pred_lms')

                self.valid_lms = tf.placeholder(tf.float32, [None, self.num_landmarks, 2], 'valid_lms')
                self.valid_pred_lms = tf.placeholder(tf.float32, [None, self.num_landmarks, 2], 'valid_pred_lms')

    def heatmaps_network(self, input_images, reuse=None, name='pred_heatmaps'):

        with tf.name_scope(name):

            if self.weight_initializer == 'xavier':
                weight_initializer = contrib.layers.xavier_initializer()
            else:
                weight_initializer = tf.random_normal_initializer(stddev=self.weight_initializer_std)

            bias_init = tf.constant_initializer(self.bias_initializer)

            with tf.variable_scope('heatmaps_network'):
                with tf.name_scope('primary_net'):

                    l1 = conv_relu_pool(input_images, 5, 128, conv_ker_init=weight_initializer, conv_bias_init=bias_init,
                                        reuse=reuse, var_scope='conv_1')
                    l2 = conv_relu_pool(l1, 5, 128, conv_ker_init=weight_initializer, conv_bias_init=bias_init,
                                        reuse=reuse, var_scope='conv_2')
                    l3 = conv_relu(l2, 5, 128, conv_ker_init=weight_initializer, conv_bias_init=bias_init,
                                   reuse=reuse, var_scope='conv_3')

                    l4_1 = conv_relu(l3, 3, 128, conv_dilation=1, conv_ker_init=weight_initializer,
                                     conv_bias_init=bias_init, reuse=reuse, var_scope='conv_4_1')
                    l4_2 = conv_relu(l3, 3, 128, conv_dilation=2, conv_ker_init=weight_initializer,
                                     conv_bias_init=bias_init, reuse=reuse, var_scope='conv_4_2')
                    l4_3 = conv_relu(l3, 3, 128, conv_dilation=3, conv_ker_init=weight_initializer,
                                     conv_bias_init=bias_init, reuse=reuse, var_scope='conv_4_3')
                    l4_4 = conv_relu(l3, 3, 128, conv_dilation=4, conv_ker_init=weight_initializer,
                                     conv_bias_init=bias_init, reuse=reuse, var_scope='conv_4_4')

                    l4 = tf.concat([l4_1, l4_2, l4_3, l4_4], 3, name='conv_4')

                    l5_1 = conv_relu(l4, 3, 256, conv_dilation=1, conv_ker_init=weight_initializer,
                                     conv_bias_init=bias_init, reuse=reuse, var_scope='conv_5_1')
                    l5_2 = conv_relu(l4, 3, 256, conv_dilation=2, conv_ker_init=weight_initializer,
                                     conv_bias_init=bias_init, reuse=reuse, var_scope='conv_5_2')
                    l5_3 = conv_relu(l4, 3, 256, conv_dilation=3, conv_ker_init=weight_initializer,
                                     conv_bias_init=bias_init, reuse=reuse, var_scope='conv_5_3')
                    l5_4 = conv_relu(l4, 3, 256, conv_dilation=4, conv_ker_init=weight_initializer,
                                     conv_bias_init=bias_init, reuse=reuse, var_scope='conv_5_4')

                    l5 = tf.concat([l5_1, l5_2, l5_3, l5_4], 3, name='conv_5')

                    l6 = conv_relu(l5, 1, 512, conv_ker_init=weight_initializer,
                                   conv_bias_init=bias_init, reuse=reuse, var_scope='conv_6')
                    l7 = conv_relu(l6, 1, 256, conv_ker_init=weight_initializer,
                                   conv_bias_init=bias_init, reuse=reuse, var_scope='conv_7')
                    primary_out = conv(l7, 1, self.num_landmarks, conv_ker_init=weight_initializer,
                                            conv_bias_init=bias_init, reuse=reuse, var_scope='conv_8')

                with tf.name_scope('fusion_net'):

                    l_fsn_0 = tf.concat([l3, l7], 3, name='conv_3_7_fsn')

                    l_fsn_1_1 = conv_relu(l_fsn_0, 3, 64, conv_dilation=1, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_1_1')
                    l_fsn_1_2 = conv_relu(l_fsn_0, 3, 64, conv_dilation=2, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_1_2')
                    l_fsn_1_3 = conv_relu(l_fsn_0, 3, 64, conv_dilation=3, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_1_3')

                    l_fsn_1 = tf.concat([l_fsn_1_1, l_fsn_1_2, l_fsn_1_3], 3, name='conv_fsn_1')

                    l_fsn_2_1 = conv_relu(l_fsn_1, 3, 64, conv_dilation=1, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_2_1')
                    l_fsn_2_2 = conv_relu(l_fsn_1, 3, 64, conv_dilation=2, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_2_2')
                    l_fsn_2_3 = conv_relu(l_fsn_1, 3, 64, conv_dilation=4, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_2_3')
                    l_fsn_2_4 = conv_relu(l_fsn_1, 5, 64, conv_dilation=3, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_2_4')

                    l_fsn_2 = tf.concat([l_fsn_2_1, l_fsn_2_2, l_fsn_2_3, l_fsn_2_4], 3, name='conv_fsn_2')

                    l_fsn_3_1 = conv_relu(l_fsn_2, 3, 128, conv_dilation=1, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_3_1')
                    l_fsn_3_2 = conv_relu(l_fsn_2, 3, 128, conv_dilation=2, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_3_2')
                    l_fsn_3_3 = conv_relu(l_fsn_2, 3, 128, conv_dilation=4, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_3_3')
                    l_fsn_3_4 = conv_relu(l_fsn_2, 5, 128, conv_dilation=3, conv_ker_init=weight_initializer,
                                          conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_3_4')

                    l_fsn_3 = tf.concat([l_fsn_3_1, l_fsn_3_2, l_fsn_3_3, l_fsn_3_4], 3, name='conv_fsn_3')

                    l_fsn_4 = conv_relu(l_fsn_3, 1, 256, conv_ker_init=weight_initializer,
                                        conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_4')
                    l_fsn_5 = conv(l_fsn_4, 1, self.num_landmarks, conv_ker_init=weight_initializer,
                                   conv_bias_init=bias_init, reuse=reuse, var_scope='conv_fsn_5')

                with tf.name_scope('upsample_net'):

                    out = deconv(l_fsn_5, 8, self.num_landmarks, conv_stride=4,
                                 conv_ker_init=deconv2d_bilinear_upsampling_initializer(
                                     [8, 8, self.num_landmarks, self.num_landmarks]), conv_bias_init=bias_init,
                                 reuse=reuse, var_scope='deconv_1')

                self.all_layers = [l1, l2, l3, l4, l5, l6, l7, primary_out, l_fsn_1, l_fsn_2, l_fsn_3, l_fsn_4,
                                   l_fsn_5, out]

                return primary_out, out

    def build_model(self):
            self.pred_hm_p, self.pred_hm_f = self.heatmaps_network(self.images,name='heatmaps_prediction')

    def create_loss_ops(self):

        def l2_loss_norm_eyes(pred_landmarks, real_landmarks, normalize=True, name='NME_loss'):

            with tf.name_scope(name):
                with tf.name_scope('real_pred_landmarks_diff'):
                    landmarks_diff = pred_landmarks - real_landmarks

                if normalize:
                    with tf.name_scope('inter_pupil_dist'):
                        with tf.name_scope('left_eye'):
                            p1 = tf.reduce_mean(tf.slice(real_landmarks, [0, 42, 0], [-1, 6, 2]), axis=1)
                        with tf.name_scope('right_eye'):
                            p2 = tf.reduce_mean(tf.slice(real_landmarks, [0, 36, 0], [-1, 6, 2]), axis=1)
                        eps = 1e-6
                        eye_dist = tf.expand_dims(tf.expand_dims(
                            tf.sqrt(tf.reduce_sum(tf.square(p1 - p2), axis=1)) + eps, axis=1), axis=1)

                    norm_landmarks_diff = landmarks_diff / eye_dist
                    l2_landmarks_norm = tf.reduce_mean(tf.square(norm_landmarks_diff))

                    out = l2_landmarks_norm
                else:
                    l2_landmarks = tf.reduce_mean(tf.square(landmarks_diff))
                    out = l2_landmarks

                return out

        if self.mode is 'TRAIN':
            primary_maps_diff = self.pred_hm_p - self.heatmaps_small
            fusion_maps_diff = self.pred_hm_f - self.heatmaps

            self.l2_primary = tf.reduce_mean(tf.square(primary_maps_diff))
            self.l2_fusion = tf.reduce_mean(tf.square(fusion_maps_diff))
            self.total_loss = self.l_weight_primary * self.l2_primary + self.l_weight_fusion * self.l2_fusion

            if self.compute_nme:
                self.nme_loss = l2_loss_norm_eyes(self.train_pred_lms, self.train_lms)
            else:
                self.nme_loss = tf.constant(0.)

            if self.valid_size > 0:
                self.valid_nme_loss = l2_loss_norm_eyes(self.valid_pred_lms,self.valid_lms)
            else:
                self.valid_nme_loss = tf.constant(0.)


        elif self.mode == 'TEST' and self.compute_nme:
            self.nme_loss = l2_loss_norm_eyes(self.pred_lms, self.lms)

    def predict_landmarks_in_batches(self,image_paths,session):

        num_batches = int(1.*len(image_paths)/self.batch_size)
        if num_batches == 0:
            batch_size = len(image_paths)
            num_batches = 1
        else:
            batch_size = self.batch_size

        img_inds = np.arange(len(image_paths))
        for j in range(num_batches):
            batch_inds = img_inds[j * self.batch_size:(j + 1) * self.batch_size]

            batch_images, _, _, batch_lms = \
                load_data(self.img_menpo_list, batch_inds, image_size=self.image_size, c_dim=self.c_dim,
                          num_landmarks=self.num_landmarks, sigma=self.sigma, scale=self.scale,
                          save_landmarks=self.compute_nme, primary=False)

            if j == 0:
                batch_maps_pred = session.run(self.pred_hm_f, {self.images: batch_images})
                all_pred_landmarks = batch_heat_maps_to_landmarks(
                    batch_maps_pred, self.batch_size, image_size=self.image_size, num_landmarks=self.num_landmarks)
                all_gt_landmarks = batch_lms
            else:
                batch_maps_pred = session.run(self.pred_hm_f, {self.images: batch_images})
                batch_pred_landmarks = batch_heat_maps_to_landmarks(
                    batch_maps_pred, self.batch_size, image_size=self.image_size, num_landmarks=self.num_landmarks)

                all_pred_landmarks = np.concatenate((all_pred_landmarks,batch_pred_landmarks),0)
                all_gt_landmarks = np.concatenate((all_gt_landmarks, batch_lms), 0)

        reminder = len(image_paths)-num_batches*batch_size

        if reminder >0:
            reminder_inds = img_inds[-reminder:]
            batch_images, _, _, batch_lms = \
                load_data(self.img_menpo_list, reminder_inds, image_size=self.image_size, c_dim=self.c_dim,
                          num_landmarks=self.num_landmarks, sigma=self.sigma, scale=self.scale,
                          save_landmarks=self.compute_nme, primary=False)

            batch_maps_pred = session.run(self.pred_hm_f, {self.images: batch_images})
            batch_pred_landmarks = batch_heat_maps_to_landmarks(
                batch_maps_pred, reminder, image_size=self.image_size, num_landmarks=self.num_landmarks)

            all_pred_landmarks = np.concatenate((all_pred_landmarks, batch_pred_landmarks), 0)
            all_gt_landmarks = np.concatenate((all_gt_landmarks, batch_lms), 0)

        return all_pred_landmarks, all_gt_landmarks

    def predict_landmarks_in_batches_loaded(self,images,session):

        num_images=int(images.shape[0])
        num_batches = int(1.*num_images/self.batch_size)
        if num_batches == 0:
            batch_size = num_images
            num_batches = 1
        else:
            batch_size = self.batch_size

        for j in range(num_batches):
            batch_images = images[j * self.batch_size:(j + 1) * self.batch_size,:,:,:]

            if j == 0:
                batch_maps_pred = session.run(self.pred_hm_f, {self.images: batch_images})
                all_pred_landmarks = batch_heat_maps_to_landmarks(
                    batch_maps_pred, self.batch_size, image_size=self.image_size, num_landmarks=self.num_landmarks)
            else:
                batch_maps_pred = session.run(self.pred_hm_f, {self.images: batch_images})
                batch_pred_landmarks = batch_heat_maps_to_landmarks(
                    batch_maps_pred, self.batch_size, image_size=self.image_size, num_landmarks=self.num_landmarks)

                all_pred_landmarks = np.concatenate((all_pred_landmarks,batch_pred_landmarks),0)

        reminder = num_images-num_batches*batch_size
        if reminder >0:
            batch_images = images[-reminder:, :, :, :]
            batch_maps_pred = session.run(self.pred_hm_f, {self.images: batch_images})
            batch_pred_landmarks = batch_heat_maps_to_landmarks(
                batch_maps_pred, reminder, image_size=self.image_size, num_landmarks=self.num_landmarks)

            all_pred_landmarks = np.concatenate((all_pred_landmarks, batch_pred_landmarks), 0)

        return all_pred_landmarks

    def create_summary_ops(self):

            var_summary = [tf.summary.histogram(var.name,var) for var in tf.trainable_variables()]
            grads = tf.gradients(self.total_loss, tf.trainable_variables())
            grads = list(zip(grads, tf.trainable_variables()))
            grad_summary = [tf.summary.histogram(var.name+'/grads',grad) for grad,var in grads]
            activ_summary = [tf.summary.histogram(layer.name, layer) for layer in self.all_layers]
            l2_primary = tf.summary.scalar('l2_primary', self.l2_primary)
            l2_fusion = tf.summary.scalar('l2_fusion', self.l2_fusion)
            l_total = tf.summary.scalar('l_total', self.total_loss)
            l_nme = tf.summary.scalar('l_nme', self.nme_loss)
            l_v_nme = tf.summary.scalar('valid_l_nme', self.valid_nme_loss)

            if self.log_histograms:
                self.batch_summary_op = tf.summary.merge(
                    [l2_primary, l2_fusion, l_total, l_nme, l_v_nme, var_summary, grad_summary, activ_summary])
            else:
                self.batch_summary_op = tf.summary.merge([l2_primary, l2_fusion, l_total, l_nme, l_v_nme])

    def eval(self):

        self.add_placeholders()
        # build model
        self.build_model()
        self.create_loss_ops()

        num_images = len(self.img_menpo_list)
        img_inds = np.arange(num_images)

        sample_iter = int(1. * num_images / self.sample_grid)

        with tf.Session(config=self.config) as sess:

            # load trained parameters
            print ('loading test model...')
            saver = tf.train.Saver()
            saver.restore(sess, self.test_model_path)

            _, model_name = os.path.split(self.test_model_path)

            for i in range(sample_iter):

                batch_inds = img_inds[i * self.sample_grid:(i + 1) * self.sample_grid]

                if self.test_data == 'art':
                    batch_images = load_art_data(self.img_menpo_list, batch_inds, image_size=self.image_size,
                                                 c_dim=self.c_dim, scale=self.scale)
                else:
                    batch_images, batch_maps_gt, batch_maps_small_gt, _ = \
                        load_data(self.img_menpo_list, batch_inds, image_size=self.image_size, c_dim=self.c_dim,
                                  num_landmarks=self.num_landmarks, sigma=self.sigma, scale=self.scale,
                                  save_landmarks=False, primary=False)

                batch_maps_small_pred, batch_maps_pred = sess.run(
                    [self.pred_hm_p,self.pred_hm_f], {self.images: batch_images})

                if self.test_data == 'art':
                    batch_maps_gt = batch_maps_pred.copy()
                    batch_maps_small_gt = batch_maps_small_pred.copy()

                sample_path_imgs = os.path.join(
                    self.save_sample_path, model_name +'-'+ self.test_data+'-sample-%d-to-%d-1.png' % (
                        i * self.sample_grid, (i + 1) * self.sample_grid))

                sample_path_channels = os.path.join(
                    self.save_sample_path, model_name +'-'+ self.test_data+ '-sample-%d-to-%d-3.png' % (
                        i * self.sample_grid, (i + 1) * self.sample_grid))

                sample_path_imgs_small = os.path.join(
                    self.save_sample_path, model_name + '-' + self.test_data + '-sample-%d-to-%d-1-s.png' % (
                        i * self.sample_grid, (i + 1) * self.sample_grid))

                sample_path_channels_small = os.path.join(
                    self.save_sample_path,model_name + '-' + self.test_data + '-sample-%d-to-%d-3-s.png' % (
                        i * self.sample_grid, (i + 1) * self.sample_grid))

                merged_img = merge_images_landmarks_maps_gt(
                    batch_images.copy(), batch_maps_pred, batch_maps_gt, image_size=self.image_size,
                    num_landmarks=self.num_landmarks, num_samples=self.sample_grid, scale=self.scale, circle_size=2,
                    test_data=self.test_data)

                map_per_channel = map_comapre_channels(
                    batch_images.copy(), batch_maps_pred, batch_maps_gt, image_size=self.image_size,
                    num_landmarks=self.num_landmarks, scale=self.scale, test_data=self.test_data)

                merged_img_small = merge_images_landmarks_maps_gt(
                    batch_images.copy(), batch_maps_small_pred, batch_maps_small_gt, image_size=self.image_size,
                    num_landmarks=self.num_landmarks, num_samples=self.sample_grid, scale=self.scale, circle_size=0,
                    test_data=self.test_data)

                map_per_channel_small = map_comapre_channels(
                    batch_images.copy(), batch_maps_small_pred, batch_maps_small_gt, image_size=self.image_size / 4,
                    num_landmarks=self.num_landmarks, scale=self.scale, test_data=self.test_data)

                scipy.misc.imsave(sample_path_imgs, merged_img)
                scipy.misc.imsave(sample_path_channels, map_per_channel)

                scipy.misc.imsave(sample_path_imgs_small, merged_img_small)
                scipy.misc.imsave(sample_path_channels_small, map_per_channel_small)

                print ('saved %s' % sample_path_imgs)
            if self.compute_nme:
                print ('\n Calculating NME on: ' + self.test_data + '...')
                pred_lms, lms_gt = self.predict_landmarks_in_batches(self.img_menpo_list, sess)
                nme = sess.run(self.nme_loss, {self.pred_lms: pred_lms, self.lms: lms_gt})
                print ('NME on ' + self.test_data + ': ' + str(nme))

    def train(self):
            tf.set_random_seed(1234)
            np.random.seed(1234)
            # build a graph
            # add placeholders
            self.add_placeholders()
            # build model
            self.build_model()
            # create loss ops
            self.create_loss_ops()
            # create summary ops
            self.create_summary_ops()

            # create optimizer and training op
            global_step = tf.Variable(0, trainable=False)
            lr = tf.train.exponential_decay(self.learning_rate,global_step, self.step, self.gamma, staircase=True)
            optimizer = tf.train.MomentumOptimizer(lr,self.momentum)

            train_op = optimizer.minimize(self.total_loss,global_step=global_step)

            with tf.Session(config=self.config) as sess:

                tf.global_variables_initializer().run()

                # create model saver and file writer
                summary_writer = tf.summary.FileWriter(logdir=self.save_log_path, graph=tf.get_default_graph())
                saver = tf.train.Saver()

                print
                print('*** Start Training ***')

                # set random seed
                epoch = 0

                num_train_images = len(self.img_menpo_list)
                if self.debug:
                    num_train_images=self.debug_data_size

                img_inds = np.arange(num_train_images)
                np.random.shuffle(img_inds)

                for step in range(self.train_iter + 1):

                    # get batch images
                    j = step % int(float(num_train_images) / float(self.batch_size))

                    if step > 0 and j == 0:
                        np.random.shuffle(img_inds)  # shuffle data if finished epoch
                        epoch += 1

                    batch_inds = img_inds[j * self.batch_size:(j + 1) * self.batch_size]

                    batch_images, batch_maps, batch_maps_small, batch_lms =\
                        load_data(self.img_menpo_list, batch_inds, image_size=self.image_size, c_dim=self.c_dim,
                                  num_landmarks=self.num_landmarks, sigma=self.sigma, scale=self.scale,
                                  save_landmarks=self.compute_nme, primary=False)

                    feed_dict_train = {self.images: batch_images, self.heatmaps: batch_maps,
                                       self.heatmaps_small: batch_maps_small}

                    sess.run(train_op, feed_dict_train)

                    # save to log and print status
                    if step == 0 or (step + 1) % self.print_every == 0:

                        if self.compute_nme is False and self.valid_size == 0:
                            feed_dict_log = feed_dict_train

                        elif self.compute_nme and self.valid_size == 0:
                            batch_maps_pred = sess.run(self.pred_hm_f, {self.images: batch_images})
                            pred_lms = batch_heat_maps_to_landmarks(
                                batch_maps_pred, self.batch_size, image_size=self.image_size,
                                num_landmarks=self.num_landmarks)

                            feed_dict_log = {
                                self.images: batch_images, self.heatmaps: batch_maps,
                                self.heatmaps_small: batch_maps_small, self.train_lms: batch_lms,
                                self.train_pred_lms: pred_lms}

                        elif self.compute_nme and self.valid_size > 0:
                            valid_pred_lms = self.predict_landmarks_in_batches_loaded(self.valid_images_loaded,sess)

                            batch_maps_pred = sess.run(self.pred_hm_f, {self.images: batch_images})
                            pred_lms = batch_heat_maps_to_landmarks(
                                batch_maps_pred, self.batch_size, image_size=self.image_size,
                                num_landmarks=self.num_landmarks)

                            feed_dict_log = {
                                self.images: batch_images, self.heatmaps: batch_maps,
                                self.heatmaps_small: batch_maps_small,
                                self.train_lms: batch_lms, self.train_pred_lms: pred_lms,
                                self.valid_lms: self.valid_landmarks_loaded,
                                self.valid_pred_lms: valid_pred_lms}

                        else:
                            valid_pred_lms = self.predict_landmarks_in_batches_loaded(self.valid_images_loaded,sess)
                            feed_dict_log = {
                                self.images: batch_images, self.heatmaps: batch_maps,
                                self.heatmaps_small: batch_maps_small,
                                self.valid_lms: self.valid_landmarks_loaded,
                                self.valid_pred_lms: valid_pred_lms}

                        summary, l_p, l_f, l_t, l_nme, l_v_nme = sess.run(
                            [self.batch_summary_op, self.l2_primary, self.l2_fusion, self.total_loss, self.nme_loss,
                             self.valid_nme_loss], feed_dict_log)

                        summary_writer.add_summary(summary, step)

                        print ('epoch: [%d] step: [%d/%d] primary loss: [%.6f] fusion loss: [%.6f] total loss: [%.6f]' % (
                            epoch, step + 1, self.train_iter, l_p, l_f, l_t))

                        print ('epoch: [%d] step: [%d/%d] NME loss: [%.6f] NME valid loss: [%.6f]' % (
                            epoch, step + 1, self.train_iter, l_nme, l_v_nme))

                    # save model
                    if (step + 1) % self.save_every == 0:
                        saver.save(sess, os.path.join(self.save_model_path, 'deep_heatmaps'), global_step=step + 1)
                        print ('model/deep-heatmaps-%d saved' % (step + 1))

                    # save images with landmarks
                    if (self.sample_every_epoch is False) and (step == 0 or (step + 1) % self.sample_every == 0):

                            batch_maps_small_pred = sess.run(self.pred_hm_p, {self.images: batch_images})
                            if not self.compute_nme:
                                batch_maps_pred = sess.run(self.pred_hm_f,  {self.images: batch_images})

                            print 'small map vals', batch_maps_small_pred.min(), batch_maps_small_pred.max()
                            print 'big map vals', batch_maps_pred.min(), batch_maps_pred.max()

                            sample_path_imgs = os.path.join(
                                self.save_sample_path,'epoch-%d-train-iter-%d-1.png' % (epoch, step + 1))
                            sample_path_ch_maps = os.path.join(
                                self.save_sample_path, 'epoch-%d-train-iter-%d-3.png' % (epoch, step + 1))
                            sample_path_imgs_small = os.path.join(
                                self.save_sample_path, 'epoch-%d-train-iter-%d-1-s.png' % (epoch, step + 1))
                            sample_path_ch_maps_small = os.path.join(
                                self.save_sample_path, 'epoch-%d-train-iter-%d-3-s.png' % (epoch, step + 1))

                            merged_img = merge_images_landmarks_maps_gt(
                                batch_images.copy(), batch_maps_pred, batch_maps, image_size=self.image_size,
                                num_landmarks=self.num_landmarks, num_samples=self.sample_grid, scale=self.scale,
                                circle_size=2)

                            map_per_channel = map_comapre_channels(
                                batch_images.copy(), batch_maps_pred, batch_maps, image_size=self.image_size,
                                num_landmarks=self.num_landmarks,scale=self.scale)

                            merged_img_small = merge_images_landmarks_maps_gt(
                                batch_images.copy(), batch_maps_small_pred, batch_maps_small,
                                image_size=self.image_size,
                                num_landmarks=self.num_landmarks, num_samples=self.sample_grid, scale=self.scale,
                                circle_size=0)

                            map_per_channel_small = map_comapre_channels(
                                batch_images.copy(), batch_maps_small_pred, batch_maps_small, image_size=self.image_size / 4,
                                num_landmarks=self.num_landmarks, scale=self.scale)

                            scipy.misc.imsave(sample_path_imgs, merged_img)
                            scipy.misc.imsave(sample_path_ch_maps, map_per_channel)
                            scipy.misc.imsave(sample_path_imgs_small, merged_img_small)
                            scipy.misc.imsave(sample_path_ch_maps_small, map_per_channel_small)

                print('*** Finished Training ***')
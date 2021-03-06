import glob
import io
import os
import tarfile
import random

import time
from flask import Blueprint, jsonify, request, current_app, send_file, url_for
from werkzeug.utils import secure_filename
#from itsdangerous import Signer, BadSignature

from mothership import models

fuzzers = Blueprint('fuzzers', __name__)

def get_best_campaign():
	for campaign in models.Campaign.all(active=True).order_by(models.Campaign.id):
		if campaign.active_fuzzers < campaign.desired_fuzzers:
			return campaign
	return None


# TODO: make instances each own a secret key used to sign submitted data
# use a wrapper on the endpoints we want the data verified for
# def get_signature(value):
# 	return Signer(current_app.config['FUZZER_KEY']).sign(str(value).encode('ascii')).decode('ascii').rsplit('.')
# def check_signature(value, signature):
# 	signed_value = str(value) + '.' + signature
# 	Signer(current_app.config['FUZZER_KEY']).unsign(signed_value)


@fuzzers.route('/fuzzers/register')
def register():
	hostname = request.args.get('hostname')
	master = request.args.get('master')

	if not master:
		campaign = get_best_campaign()
		if not campaign:
			return 'No active campaigns', 404
		instance = models.FuzzerInstance.create(hostname=hostname)
		instance.start_time = time.time()
		campaign.fuzzers.append(instance)
		campaign.commit()
	else:
		campaign = models.Campaign.get(id=master)
		if not campaign:
			return 'Could not find specified campaign', 404
		if campaign.fuzzers.filter_by(master=True).first():
			return 'Campaign already has a master', 400
		instance = models.FuzzerInstance.create(hostname=hostname, master=True)
		instance.start_time = time.time()
		campaign.fuzzers.append(instance)
		campaign.commit()

	# avoid all hosts uploading at the same time from reporting at the same time
	deviation = random.randint(15, 30)
	return jsonify(
		id=instance.id,
		name=secure_filename(instance.name),
		program=campaign.executable_name,
		program_args=campaign.executable_args.split(' ') if campaign.executable_args else [],  # TODO: add support for spaces
		args=campaign.afl_args.split(' ') if campaign.afl_args else [],

		campaign_id=campaign.id,
		campaign_name=secure_filename(campaign.name),

		download=request.host_url[:-1] + url_for('fuzzers.download', campaign_id=campaign.id),
		submit=request.host_url[:-1] + url_for('fuzzers.submit', instance_id=instance.id),
		submit_crash=request.host_url[:-1] + url_for('fuzzers.submit_crash', instance_id=instance.id),
		upload=request.host_url[:-1] + url_for('fuzzers.upload', instance_id=instance.id),
		upload_in=current_app.config['UPLOAD_FREQUENCY'] + deviation
	)


@fuzzers.route('/fuzzers/terminate/<int:instance_id>', methods=['POST'])
def terminate(instance_id):
	instance = models.FuzzerInstance.get(id=instance_id)
	instance.update(terminated=True)
	instance.commit()
	return jsonify()


@fuzzers.route('/fuzzers/is_active/<int:campaign_id>', methods=['GET'])
def is_active(campaign_id):
	campaign = models.Campaign.get(id=campaign_id)
	if not campaign:
		return 'Campaign not found', 404
	return jsonify(active=bool(campaign.active and campaign.active_fuzzers))


@fuzzers.route('/fuzzers/submit/<int:instance_id>', methods=['POST'])
def submit(instance_id):
	instance = models.FuzzerInstance.get(id=instance_id)
	instance.update(**request.json['status'])
	for snapshot_data in request.json['snapshots']:
		snapshot = models.FuzzerSnapshot()
		snapshot.update(**snapshot_data)
		instance.snapshots.append(snapshot)
	instance.commit()
	return jsonify(
		terminate=not instance.campaign.active
	)


@fuzzers.route('/fuzzers/submit_crash/<int:instance_id>', methods=['POST'])
def submit_crash(instance_id):
	instance = models.FuzzerInstance.get(id=instance_id)
	campaign = instance.campaign
	for filename, file in request.files.items():
		crash = models.Crash.create(
			instance_id=instance.id,
			campaign_id=instance.campaign_id,
			created=request.args.get('time'),
			name=file.filename,
			analyzed=False
		)
		crash_dir = os.path.join(current_app.config['DATA_DIRECTORY'], secure_filename(campaign.name), 'crashes')
		os.makedirs(crash_dir, exist_ok=True)
		upload_path = os.path.join(crash_dir, '%d_%s' % (crash.id, secure_filename(file.filename.replace(',', '_'))))
		file.save(upload_path)
		crash.path = os.path.abspath(upload_path)
		crash.commit()
	return ''


@fuzzers.route('/fuzzers/submit_analysis/<int:crash_id>', methods=['POST'])
def submit_analysis(crash_id):
	crash = models.Crash.get(id=crash_id)
	if not crash:
		return 'Crash not found', 404
	crash.crash_in_debugger = request.json['crash']
	crash.analyzed = True
	if crash.crash_in_debugger:
		crash.address = request.json['pc']
		crash.backtrace = ', '.join(str(frame['address']) for frame in request.json['frames'])
		crash.faulting_instruction = request.json['faulting instruction']
		crash.exploitable = request.json['exploitable']['Exploitability Classification']
		crash.exploitable_hash = request.json['exploitable']['Hash']
		crash.exploitable_data = request.json['exploitable']
		crash.frames = request.json['frames']

	crash.commit()
	return ''


@fuzzers.route('/fuzzers/upload/<int:instance_id>', methods=['POST'])
def upload(instance_id):
	instance = models.FuzzerInstance.get(id=instance_id)
	campaign = instance.campaign
	data_dir = current_app.config['DATA_DIRECTORY']
	sync_dir = os.path.join(data_dir, secure_filename(campaign.name), 'sync_dir')
	os.makedirs(sync_dir, exist_ok=True)
	request.files['file'].save(os.path.join(sync_dir, secure_filename(instance.name) + '.tar'))
	return jsonify(
		upload_in=current_app.config['UPLOAD_FREQUENCY'],
	)


@fuzzers.route('/fuzzers/download/<int:campaign_id>', methods=['GET'])
def download(campaign_id):
	campaign = models.Campaign.get(id=campaign_id)
	sync_dir = os.path.join(current_app.config['DATA_DIRECTORY'], secure_filename(campaign.name), 'sync_dir', '*.tar')
	return jsonify(
		executable=request.host_url[:-1] + url_for('fuzzers.download_executable', campaign_id=campaign.id),
		libraries=request.host_url[:-1] + url_for('fuzzers.download_libraries', campaign_id=campaign.id),
		testcases=request.host_url[:-1] + url_for('fuzzers.download_testcases', campaign_id=campaign.id),
		ld_preload=request.host_url[:-1] + url_for('fuzzers.download_ld_preload', campaign_id=campaign.id),
		dictionary=request.host_url[:-1] + url_for('fuzzers.download_dictionary', campaign_id=campaign.id) if campaign.has_dictionary else None,
		sync_dirs=[
			request.host_url[:-1] + url_for('fuzzers.download_syncdir', campaign_id=campaign.id, filename=os.path.basename(filename)) for filename in glob.glob(sync_dir)
		],
		sync_in=current_app.config['DOWNLOAD_FREQUENCY'],
	)

@fuzzers.route('/fuzzers/download/<int:campaign_id>/testcases.tar', methods=['GET'])
def download_testcases(campaign_id):
	campaign = models.Campaign.get(id=campaign_id)
	testcases_local_dir = os.path.join(current_app.config['DATA_DIRECTORY'], secure_filename(campaign.name), 'testcases')
	return serve_directory_tar(testcases_local_dir, 'testcases')


@fuzzers.route('/fuzzers/download/<int:campaign_id>/ld_preload.tar', methods=['GET'])
def download_ld_preload(campaign_id):
	campaign = models.Campaign.get(id=campaign_id)
	testcases_local_dir = os.path.join(current_app.config['DATA_DIRECTORY'], secure_filename(campaign.name), 'ld_preload')
	return serve_directory_tar(testcases_local_dir, 'ld_preload')

@fuzzers.route('/fuzzers/download/<int:campaign_id>/<filename>', methods=['GET'])
def download_syncdir(campaign_id, filename):
	campaign = models.Campaign.get(id=campaign_id)
	syncdir = os.path.join(current_app.config['DATA_DIRECTORY'], secure_filename(campaign.name), 'sync_dir')
	tar = os.path.join(syncdir, secure_filename(filename.rsplit('.', 1)[0]) + '.tar')
	return send_file(os.path.abspath(tar))

@fuzzers.route('/fuzzers/download/<int:campaign_id>/libraries.tar', methods=['GET'])
def download_libraries(campaign_id):
	campaign = models.Campaign.get(id=campaign_id)
	libraries_local_dir = os.path.join(current_app.config['DATA_DIRECTORY'], secure_filename(campaign.name), 'libraries')
	return serve_directory_tar(libraries_local_dir, 'libraries')

@fuzzers.route('/fuzzers/download/<int:campaign_id>/executable', methods=['GET'])
def download_executable(campaign_id):
	campaign = models.Campaign.get(id=campaign_id)
	executable = os.path.join(current_app.config['DATA_DIRECTORY'], secure_filename(campaign.name), 'executable')
	return send_file(os.path.abspath(executable))

@fuzzers.route('/fuzzers/download/<int:campaign_id>/dictionary.txt', methods=['GET'])
def download_dictionary(campaign_id):
	campaign = models.Campaign.get(id=campaign_id)
	executable = os.path.join(current_app.config['DATA_DIRECTORY'], secure_filename(campaign.name), 'dictionary')
	return send_file(os.path.abspath(executable))

@fuzzers.route('/fuzzers/download/afl-fuzz', methods=['GET'])
def download_afl():
	afl = os.path.join(current_app.config['DATA_DIRECTORY'], 'afl-fuzz')
	return send_file(os.path.abspath(afl))


def serve_directory_tar(local_dir, arcname):
	tardata = io.BytesIO()
	os.makedirs(local_dir, exist_ok=True)
	with tarfile.open(fileobj=tardata, mode='w:') as tar:
		tar.add(local_dir, arcname=arcname)
	tardata.seek(0)
	return send_file(tardata)


@fuzzers.route('/fuzzers/analysis_queue/<int:campaign_id>')
def analysis_queue(campaign_id):
	campaign = models.Campaign.get(id=campaign_id)
	return jsonify(
		program=campaign.executable_name,
		program_args=campaign.executable_args.split(' ') if campaign.executable_args else [],  # TODO: add support for spaces
		crashes=[{
			'crash_id': crash.id,
			'download': request.host_url[:-1] + url_for('fuzzers.download_crash', crash_id=crash.id)
		} for crash in campaign.crashes.filter_by(analyzed=False)]
	)

@fuzzers.route('/fuzzers/download_crash/<int:crash_id>')
def download_crash(crash_id):
	crash = models.Crash.get(id=crash_id)
	if not crash:
		return 'Crash not found', 404
	return send_file(crash.path)



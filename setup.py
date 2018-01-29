from setuptools import setup

setup(
    name='VF-1',
    version='0.0.5',
    description="Command line gopher client. High speed, low drag.",
    author="Solderpunk",
    author_email="solderpunk@sdf.org",
    url='https://github.com/solderpunk/VF-1',
    classifiers=[
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python :: 3 :: Only',
        'Topic :: Communications',
        'Intended Audience :: End Users/Desktop',
        'Environment :: Console',
        'Development Status :: 4 - Beta',
    ],
    py_modules = ["vf1"],
    entry_points={
        "console_scripts": ["vf1=vf1:main"]
    },
    install_requires=[],
)
